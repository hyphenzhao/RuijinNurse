import json
import logging
import math
import socket
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError
from django.http import JsonResponse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.views.decorators.http import require_POST
from requests.exceptions import Timeout
from urllib3.exceptions import TimeoutError as UpstreamTimeout

from .models import QATiming


logger = logging.getLogger(__name__)
_registry_lock = threading.RLock()
_active_timers = {}
_pending_finishes = {}


def ollama_request_timeout():
    """Allow slow CPU thinking while bounding a dead upstream connection."""
    try:
        read_timeout = float(getattr(settings, 'OLLAMA_READ_TIMEOUT_SECONDS', 900))
        if not math.isfinite(read_timeout) or read_timeout <= 0:
            raise ValueError
    except (TypeError, ValueError):
        read_timeout = 900
    return (10, read_timeout)


def _session_tag(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return ''
    # JWT requests have no browser session. Keep their ownership namespace separate.
    if hasattr(request, 'auth'):
        identity = f'api:{user.pk}'
    else:
        session = getattr(request, 'session', None)
        key = session.session_key if session is not None else None
        if not key:
            return ''
        identity = f'{user.pk}:{key}'
    return salted_hmac('promotions.qa_timing', identity, algorithm='sha256').hexdigest()


def _close_upstream(response):
    """Wake a blocked urllib3 reader before closing its response.

    Response.close() alone can wait for another thread's buffered read lock. The
    socket shutdown releases that read immediately; plain close is the fallback
    for mock/custom response transports without the requests socket hierarchy.
    """
    if response is None:
        return
    raw = getattr(response, 'raw', None)
    connection = getattr(raw, '_connection', None)
    sock = getattr(connection, 'sock', None)
    if sock is None:
        fp = getattr(raw, '_fp', None)
        buffered = getattr(fp, 'fp', None)
        transport = getattr(buffered, 'raw', None)
        sock = getattr(transport, '_sock', None)
    if isinstance(sock, socket.socket):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    close = getattr(response, 'close', None)
    if close:
        try:
            close()
        except (OSError, ValueError):
            pass


def flush_pending_timings():
    """Retry small timing-only writes; no question or answer data is retained."""
    with _registry_lock:
        timers = list(_pending_finishes.values())
    for timer in timers:
        timer._persist_finish()


class QATimer:
    def __init__(self, request, model_key, started_ns, started_at, source='web'):
        flush_pending_timings()
        self.started_ns = started_ns
        self.first_answer_ms = None
        self.model_name = ''
        self.timing_id = None
        self.finished = False
        self.session_tag = _session_tag(request)
        self._lock = threading.RLock()
        self._cancelled = threading.Event()
        self._response = None
        self._finish_payload = None
        try:
            self.timing_id = QATiming.objects.create(
                session_tag=self.session_tag, model_key=model_key[:150],
                source=source, started_at=started_at,
            ).pk
            with _registry_lock:
                _active_timers[self.timing_id] = self
        except DatabaseError:
            logger.warning('Could not create question timing')

    @property
    def cancelled(self):
        return self._cancelled.is_set()

    def elapsed_ms(self):
        return (time.perf_counter_ns() - self.started_ns) // 1_000_000

    def first_answer(self, content):
        if self.first_answer_ms is None and content.strip():
            self.first_answer_ms = self.elapsed_ms()

    @contextmanager
    def track_response(self, response):
        with self._lock:
            self._response = response
            cancelled = self.cancelled
        try:
            if cancelled:
                _close_upstream(response)
                raise InterruptedError('Question was cancelled')
            with response:
                yield response
        finally:
            with self._lock:
                if self._response is response:
                    self._response = None

    def cancel(self):
        with self._lock:
            if self.finished:
                return
            self._cancelled.set()
            response = self._response
        _close_upstream(response)
        self.finish('cancelled')

    def close_upstream(self):
        with self._lock:
            response = self._response
        _close_upstream(response)

    def _persist_finish(self):
        with self._lock:
            if self.finished or self._finish_payload is None:
                return
            if self.timing_id is None:
                self.finished = True
                return
            try:
                # A stop request may already have won the transition from pending.
                QATiming.objects.filter(pk=self.timing_id, status='pending').update(**self._finish_payload)
            except DatabaseError:
                with _registry_lock:
                    _pending_finishes[self.timing_id] = self
                logger.warning('Could not finish question timing; retry queued')
                return
            self.finished = True
            with _registry_lock:
                _pending_finishes.pop(self.timing_id, None)
                _active_timers.pop(self.timing_id, None)

    def finish(self, status, record=None):
        with self._lock:
            if self.finished:
                # /stop may have finalized before the request's QA record exists.
                if record is not None and self.timing_id:
                    try:
                        QATiming.objects.filter(pk=self.timing_id, qa_record__isnull=True).update(qa_record_id=record.pk)
                    except DatabaseError:
                        logger.warning('Could not link completed question timing')
                return
            if self._finish_payload is None or self.cancelled:
                self._finish_payload = dict(
                    status='cancelled' if self.cancelled else status,
                    finished_at=timezone.now(), backend_ms=self.elapsed_ms(),
                    first_answer_ms=self.first_answer_ms, model_name=self.model_name[:150],
                    qa_record_id=record.pk if record else None,
                )
        self._persist_finish()


class TimedStream:
    """Finish timing and close the upstream when the client disconnects."""

    def __init__(self, iterator, timer):
        self.iterator = iterator
        self.timer = timer

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self.iterator)
        except StopIteration:
            self.timer.finish('interrupted')
            raise
        except Exception:
            self.timer.finish('failed')
            raise

    def close(self):
        self.timer.close_upstream()
        try:
            self.iterator.close()
        finally:
            self.timer.finish('interrupted')


def failure_status(error):
    # requests may wrap a read timeout raised while iterating the response.
    current = error
    while current is not None:
        if isinstance(current, (Timeout, UpstreamTimeout)) or any(
            isinstance(arg, UpstreamTimeout) for arg in current.args
        ):
            return 'timeout'
        current = current.__cause__ or current.__context__
    return 'failed'


def _payload(request):
    try:
        if hasattr(request, 'data'):
            body = request.data
        else:
            if len(request.body) > 2048:
                return {}, None
            body = json.loads(request.body)
        if not isinstance(body, dict) or not isinstance(body.get('timing_id'), str):
            return {}, None
        return body, uuid.UUID(body['timing_id'])
    except (TypeError, ValueError):
        return {}, None


def cancel_active_for_tag(request):
    tag = _session_tag(request)
    if not tag:
        return 0
    with _registry_lock:
        timers = [timer for timer in _active_timers.values() if timer.session_tag == tag]
    for timer in timers:
        timer.cancel()
    return len(timers)


def cancel_timing(request):
    _, timing_id = _payload(request)
    tag = _session_tag(request)
    if not timing_id or not tag or not request.user.is_authenticated:
        return
    with _registry_lock:
        timer = _active_timers.get(timing_id)
    if timer is not None and timer.session_tag == tag:
        timer.cancel()
    try:
        row = QATiming.objects.filter(pk=timing_id, session_tag=tag)
        now = timezone.now()
        if not row.filter(status='pending').update(status='cancelled', finished_at=now):
            # Browser abort can close the WSGI iterator just before the stop POST.
            row.filter(status='interrupted', finished_at__gte=now - timedelta(minutes=1)).update(status='cancelled')
    except DatabaseError:
        logger.warning('Could not cancel question timing')


@login_required
@require_POST
def qa_timing_report(request):
    body, timing_id = _payload(request)
    first = body.get('first_ms')
    total = body.get('total_ms')
    tag = _session_tag(request)
    if not timing_id or not tag or type(first) is not int or type(total) is not int or not 0 <= first <= total <= 86_400_000:
        return JsonResponse({'ok': False, 'error': '计时参数无效'}, status=400)

    rows = QATiming.objects.filter(pk=timing_id, session_tag=tag, status='success')
    try:
        if not rows.filter(client_total_ms__isnull=True).update(client_first_ms=first, client_total_ms=total):
            if not rows.exists():
                return JsonResponse({'ok': False, 'error': '计时记录不可用'}, status=404)
    except DatabaseError:
        return JsonResponse({'ok': False, 'error': '计时暂时不可用'}, status=503)
    return JsonResponse({'ok': True})
