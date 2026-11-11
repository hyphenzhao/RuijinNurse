import csv
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from statistics import median

from django.contrib.auth.decorators import login_required
from django.db.models import CharField, Count, Value
from django.db.models.functions import Coalesce, NullIf
from django.http import JsonResponse, StreamingHttpResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .models import QATiming
from .qa_timing import flush_pending_timings


PAGE_SIZE = 20
METRICS = ('client_total_ms', 'client_first_ms', 'backend_ms')
DETAIL_FIELDS = (
    'request_id', 'started_at', 'finished_at', 'source', 'timing_model', 'status',
    'client_first_ms', 'client_total_ms', 'first_answer_ms', 'backend_ms',
)
STATUS_LABELS = dict(QATiming.STATUS_CHOICES)


def _filters(request):
    today = timezone.localdate()
    start = date.fromisoformat(request.GET.get('start') or (today - timedelta(days=29)).isoformat())
    end = date.fromisoformat(request.GET.get('end') or today.isoformat())
    if end < start or (end - start).days >= 366 or end == date.max:
        raise ValueError('请选择不超过 366 天的有效日期范围。')
    model = request.GET.get('model', '').strip()
    if len(model) > 150:
        raise ValueError('模型名称过长。')
    source = request.GET.get('source', '').strip()
    if source not in ('', 'web', 'api'):
        raise ValueError('未知的问答来源。')

    zone = timezone.get_current_timezone()
    window = QATiming.objects.filter(
        started_at__gte=datetime.combine(start, time.min, tzinfo=zone),
        started_at__lt=datetime.combine(end + timedelta(days=1), time.min, tzinfo=zone),
    ).annotate(timing_model=Coalesce(NullIf('model_name', Value('')), 'model_key', output_field=CharField()))
    if source:
        window = window.filter(source=source)
    return start, end, model, window


def _summary(values):
    if not values:
        return {'count': 0, 'average': None, 'median': None, 'p95': None}
    values.sort()
    count = len(values)
    middle = count // 2
    midpoint = values[middle] if count % 2 else (values[middle - 1] + values[middle]) / 2
    return {
        'count': count,
        'average': round(sum(values) / count, 1),
        'median': midpoint,
        'p95': values[math.ceil(count * 0.95) - 1],
    }


def _records(query):
    return query.order_by('-started_at', '-request_id').values(*DETAIL_FIELDS)


def _format_time(value):
    return timezone.localtime(value).isoformat(sep=' ', timespec='seconds') if value is not None else None


@login_required
@require_GET
@never_cache
def timing_analytics_data(request):
    flush_pending_timings()
    try:
        start, end, model, window = _filters(request)
        page = max(1, int(request.GET.get('page', 1)))
    except (ValueError, OverflowError):
        return JsonResponse({'ok': False, 'error': '请检查页码、模型和日期。日期范围最多 366 天。'}, status=400)

    query = window.filter(timing_model=model) if model else window
    counts = dict(query.order_by().values('status').annotate(count=Count('pk')).values_list('status', 'count'))
    total = sum(counts.values())
    page_count = max(1, math.ceil(total / PAGE_SIZE))
    page = min(page, page_count)
    failed = counts.get('failed', 0) + counts.get('timeout', 0)
    assessed = failed + counts.get('success', 0)

    metrics = {name: [] for name in METRICS}
    daily_client = defaultdict(list)
    daily_backend = defaultdict(list)
    samples = query.filter(status='success').order_by().values_list('started_at', *METRICS)
    for started_at, client_total, client_first, backend in samples.iterator(chunk_size=1000):
        for name, value in zip(METRICS, (client_total, client_first, backend)):
            if value is not None:
                metrics[name].append(value)
        day = timezone.localdate(started_at)
        if client_total is not None:
            daily_client[day].append(client_total)
        if backend is not None:
            daily_backend[day].append(backend)

    trend = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        client = _summary(daily_client.get(day, []))
        backend = daily_backend.get(day)
        trend.append({
            'date': day.isoformat(), 'client_median': client['median'], 'client_p95': client['p95'],
            'backend_median': median(backend) if backend else None,
        })

    records = list(_records(query)[(page - 1) * PAGE_SIZE:page * PAGE_SIZE])
    for row in records:
        row['started_at'] = _format_time(row['started_at'])
        row['finished_at'] = _format_time(row['finished_at'])
        row['status_label'] = STATUS_LABELS.get(row['status'], row['status'])
    models = list(window.exclude(timing_model='').order_by('timing_model').values_list('timing_model', flat=True).distinct()[:500])
    return JsonResponse({'ok': True, 'data': {
        'metrics': {name: _summary(values) for name, values in metrics.items()},
        'counts': counts,
        'failure_rate': failed / assessed if assessed else None,
        'models': models,
        'trend': trend,
        'records': records,
        'page': page,
        'page_count': page_count,
        'total': total,
    }})


def _spreadsheet_text(value):
    # Model names can be configured by users. Keep them literal in spreadsheet programs.
    value = value or ''
    if value.lstrip().startswith(('=', '+', '-', '@', '＝', '＋', '－', '＠')) or value.startswith(('\t', '\r', '\n')):
        return "'" + value
    return value


class _CsvBuffer:
    def write(self, value):
        return value


@login_required
@require_GET
@never_cache
def timing_analytics_export(request):
    flush_pending_timings()
    try:
        start, end, model, window = _filters(request)
    except (ValueError, OverflowError):
        return JsonResponse({'ok': False, 'error': '请检查模型和日期。日期范围最多 366 天。'}, status=400)

    query = window.filter(timing_model=model) if model else window

    def rows():
        writer = csv.writer(_CsvBuffer(), quoting=csv.QUOTE_ALL)
        yield '\ufeff'
        yield writer.writerow([
            '请求编号', '开始时间', '结束时间', '来源', '模型', '状态', '首字等待(ms)',
            '完整回答等待(ms)', '后台首字(ms)', '后台处理(ms)',
        ])
        for row in _records(query).iterator(chunk_size=1000):
            yield writer.writerow([
                row['request_id'], _format_time(row['started_at']), _format_time(row['finished_at']),
                _spreadsheet_text(row['source']), _spreadsheet_text(row['timing_model']),
                _spreadsheet_text(STATUS_LABELS.get(row['status'], row['status'])),
                row['client_first_ms'], row['client_total_ms'], row['first_answer_ms'], row['backend_ms'],
            ])

    response = StreamingHttpResponse(rows(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="qa-timing-{start.isoformat()}-{end.isoformat()}.csv"'
    return response
