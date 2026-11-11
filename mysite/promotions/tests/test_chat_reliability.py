"""Synthetic regressions for patient isolation, incomplete answers and cancellation."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from promotions import qa_timing, views
from promotions.models import Patient, QATiming


class ChatReliabilityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('synthetic-reliability')
        self.client.force_login(self.user)
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.models = [('model:synthetic', '模拟模型')]
        for name, value in {
            'RUNTIME_SESSIONS_DIR': Path(self.directory.name),
            '_get_runtime_model_choices': MagicMock(return_value=self.models),
            '_get_chat_target': MagicMock(return_value={
                'model_name': 'synthetic', 'base_url': 'http://example.invalid',
                'role_text': '', 'knowledge_text': '',
            }),
            'load_role_and_knowledge': MagicMock(return_value=('', '')),
        }.items():
            mock = patch(f'promotions.views.{name}', value)
            mock.start()
            self.addCleanup(mock.stop)
        self.a = Patient.objects.create(name='合成患者甲', bed_no='T1')
        self.b = Patient.objects.create(name='合成患者乙', bed_no='T2')

    def select(self, patient):
        return self.client.post(reverse('patient_select'), {
            'patient_id': patient.pk if patient else None, 'model_key': 'model:synthetic',
        }, content_type='application/json')

    def stream(self, question, text='完整合成答复', done=True):
        provider = MagicMock()
        provider.__enter__.return_value = provider
        chunks = [{'message': {'content': text}, 'done': done}]
        provider.iter_lines.return_value = iter(map(json.dumps, chunks))
        with patch('promotions.views._stream_ollama_response_by_target', return_value=provider) as mock:
            response = self.client.post(reverse('promotion_stream'), {
                'promotion_text': question, 'model_select': 'model:synthetic',
            })
            body = b''.join(response.streaming_content).decode()
            response.close()
        return mock.call_args.args[2], body

    @override_settings(OLLAMA_READ_TIMEOUT_SECONDS=37)
    def test_model_transport_uses_configured_read_timeout(self):
        with patch('promotions.views.requests.post') as post:
            views._stream_ollama_response_by_target('http://example.invalid', 'synthetic', [])
        self.assertEqual(post.call_args.kwargs['timeout'], (10, 37))

    def test_patient_switch_and_return_only_restore_that_patient(self):
        self.select(self.a)
        self.stream('甲专属问题', '甲专属答案')
        selected_b = self.select(self.b).json()
        self.assertEqual(selected_b['history'], [])
        messages, _ = self.stream('乙专属问题', '乙专属答案')
        self.assertNotIn('甲专属', json.dumps(messages, ensure_ascii=False))
        selected_a = self.select(self.a).json()
        self.assertEqual(selected_a['history'], [{'user': '甲专属问题', 'response': '甲专属答案'}])
        messages, _ = self.stream('甲的后续问题')
        self.assertIn('甲专属问题', json.dumps(messages, ensure_ascii=False))
        self.assertNotIn('乙专属', json.dumps(messages, ensure_ascii=False))

    def test_unassigned_patient_is_also_isolated_and_legacy_file_preserved(self):
        session_key = self.client.session.session_key
        legacy = Path(self.directory.name) / f'{session_key}__model_synthetic.json'
        legacy.write_text(json.dumps({'messages': [{'role': 'assistant', 'content': '旧混合上下文'}]}))
        messages, _ = self.stream('未关联问题')
        self.assertNotIn('旧混合上下文', json.dumps(messages, ensure_ascii=False))
        self.assertTrue(legacy.exists())
        self.select(self.a)
        messages, _ = self.stream('甲新问题')
        self.assertNotIn('未关联问题', json.dumps(messages, ensure_ascii=False))
        self.assertEqual(len(self.select(None).json()['history']), 1)

    def test_partial_and_empty_answers_do_not_enter_next_model_context(self):
        self.select(self.a)
        _, body = self.stream('中断的问题', '截断的答案', done=False)
        self.assertIn('"result": "interrupted"', body)
        _, body = self.stream('空白答案的问题', '', done=True)
        self.assertIn('"result": "failed"', body)
        messages, _ = self.stream('成功的新问题')
        history = json.dumps(messages, ensure_ascii=False)
        for invalid in ('中断的问题', '截断的答案', '空白答案的问题'):
            self.assertNotIn(invalid, history)
        self.assertEqual(len(self.select(self.a).json()['history']), 1)

    def test_page_refresh_preserves_isolated_history(self):
        self.select(self.a)
        self.stream('刷新保留的问题')
        response = self.client.get(reverse('promotion'))
        self.assertContains(response, '刷新保留的问题')
        messages, _ = self.stream('刷新后继续')
        self.assertIn('刷新保留的问题', json.dumps(messages, ensure_ascii=False))


    def test_disconnect_keeps_failed_record_link_without_context_pollution(self):
        self.select(self.a)
        provider = MagicMock()
        provider.__enter__.return_value = provider
        provider.iter_lines.return_value = iter([
            json.dumps({'message': {'content': '未完成'}, 'done': False}),
            json.dumps({'message': {'content': '不可见后续'}, 'done': True}),
        ])
        with patch('promotions.views._stream_ollama_response_by_target', return_value=provider):
            response = self.client.post(reverse('promotion_stream'), {
                'promotion_text': '断开的问题', 'model_select': 'model:synthetic',
            })
            iterator = iter(response.streaming_content)
            next(iterator)  # status
            next(iterator)  # first answer fragment
            response.close()
        timing = QATiming.objects.get()
        self.assertEqual(timing.status, 'interrupted')
        self.assertIsNotNone(timing.qa_record_id)
        self.assertEqual(timing.qa_record.patient_id, self.a.pk)
        self.assertEqual(self.select(self.a).json()['history'], [])

    def test_stop_before_provider_prevents_request_and_links_cancelled_record(self):
        with patch('promotions.views._stream_ollama_response_by_target') as provider:
            response = self.client.post(reverse('promotion_stream'), {
                'promotion_text': '立即停止', 'model_select': 'model:synthetic',
            })
            iterator = iter(response.streaming_content)
            next(iterator)
            timing = QATiming.objects.get()
            self.client.post(reverse('promotion_stop'), {'timing_id': str(timing.pk)}, content_type='application/json')
            body = b''.join(iterator).decode()
            response.close()
        provider.assert_not_called()
        timing.refresh_from_db()
        self.assertEqual(timing.status, 'cancelled')
        self.assertIsNotNone(timing.qa_record_id)
        self.assertIn('"result": "cancelled"', body)


class TimerRecoveryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('synthetic-timer')
        self.request = SimpleNamespace(user=self.user, auth='synthetic-jwt', session=None)
        self.addCleanup(qa_timing._active_timers.clear)
        self.addCleanup(qa_timing._pending_finishes.clear)

    def timer(self):
        return qa_timing.QATimer(self.request, 'model:synthetic', time.perf_counter_ns(), timezone.now(), source='api')

    def test_temporary_finish_write_failure_retries_original_success(self):
        timer = self.timer()
        with patch('django.db.models.query.QuerySet.update', side_effect=DatabaseError('synthetic busy')):
            timer.finish('success')
        self.assertFalse(timer.finished)
        self.assertEqual(QATiming.objects.get().status, 'pending')
        timer.finish('interrupted')
        self.assertTrue(timer.finished)
        self.assertEqual(QATiming.objects.get().status, 'success')

    def test_next_request_retries_a_finish_after_stream_was_closed(self):
        timer = self.timer()
        with patch('django.db.models.query.QuerySet.update', side_effect=DatabaseError('synthetic busy')):
            timer.finish('failed')
        next_timer = self.timer()
        self.assertEqual(QATiming.objects.get(pk=timer.timing_id).status, 'failed')
        next_timer.finish('success')

    def test_startup_recovery_marks_orphans_without_inventing_duration(self):
        timer = self.timer()
        call_command('recover_question_timings', older_than_minutes=0, verbosity=0)
        timing = QATiming.objects.get(pk=timer.timing_id)
        self.assertEqual(timing.status, 'interrupted')
        self.assertIsNone(timing.backend_ms)
        self.assertIsNotNone(timing.finished_at)

    def test_stop_only_closes_same_owner(self):
        timer = self.timer()
        response = MagicMock()
        with timer.track_response(response):
            other_user = get_user_model().objects.create_user('synthetic-other')
            other = SimpleNamespace(user=other_user, auth='token', data={'timing_id': str(timer.timing_id)})
            qa_timing.cancel_timing(other)
            self.assertFalse(timer.cancelled)
            self.request.data = {'timing_id': str(timer.timing_id)}
            qa_timing.cancel_timing(self.request)
            self.assertTrue(timer.cancelled)
            response.close.assert_called_once()
        self.assertEqual(QATiming.objects.get().status, 'cancelled')

    def test_stop_interrupts_real_blocked_upstream_socket(self):
        first_read = threading.Event()
        release_server = threading.Event()
        reader_done = threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-ndjson')
                self.end_headers()
                self.wfile.write(b'{"message":{"content":"x"}}\n')
                self.wfile.flush()
                release_server.wait(5)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        timer = self.timer()
        response = requests.get(f'http://127.0.0.1:{server.server_port}/', stream=True, timeout=5)
        def consume():
            try:
                with timer.track_response(response):
                    for _ in response.iter_lines(chunk_size=1):
                        first_read.set()
            except Exception:
                pass  # shutdown is expected to interrupt the in-flight reader
            finally:
                reader_done.set()
        reader = threading.Thread(target=consume, daemon=True)
        reader.start()
        try:
            self.assertTrue(first_read.wait(2))
            timer.cancel()
            self.assertTrue(reader_done.wait(1), 'Upstream read was not interrupted by stop')
            self.assertEqual(QATiming.objects.get().status, 'cancelled')
        finally:
            release_server.set()
            response.close()
            server.shutdown()
            server.server_close()
            reader.join(timeout=2)


class RoleKnowledgeFallbackTests(SimpleTestCase):
    def test_example_fallback_and_existing_files_take_priority_including_empty(self):
        from promotions.view_helper import load_role_and_knowledge
        with TemporaryDirectory() as directory:
            base = Path(directory)
            self.assertEqual(load_role_and_knowledge(directory), ('', ''))
            (base / 'role.example.txt').write_text('示例角色', encoding='utf-8')
            (base / 'knowledge.example.txt').write_text('示例知识', encoding='utf-8')
            self.assertEqual(load_role_and_knowledge(directory), ('示例角色', '示例知识'))
            (base / 'role.txt').write_text('现场角色', encoding='utf-8')
            (base / 'knowledge.txt').write_text('', encoding='utf-8')
            self.assertEqual(load_role_and_knowledge(directory), ('现场角色', ''))
