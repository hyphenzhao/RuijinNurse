import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from requests.exceptions import ReadTimeout

from promotions.models import QATiming


class QuestionTimingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('test-timing')
        self.client.force_login(self.user)
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for name, value in {
            'RUNTIME_SESSIONS_DIR': Path(directory.name),
            '_get_runtime_model_choices': MagicMock(return_value=[('model:synthetic', '模拟模型')]),
            '_get_chat_target': MagicMock(return_value={
                'model_name': 'synthetic', 'base_url': 'http://example.invalid',
                'role_text': '', 'knowledge_text': '',
            }),
            'load_role_and_knowledge': MagicMock(return_value=('', '')),
            '_save_latest_response_text': MagicMock(),
        }.items():
            mock = patch(f'promotions.views.{name}', value)
            mock.start()
            self.addCleanup(mock.stop)

    def stream(self, chunks=None, error=None):
        provider = MagicMock()
        provider.__enter__.return_value = provider
        if error:
            provider.iter_lines.side_effect = error
        else:
            provider.iter_lines.return_value = iter(map(json.dumps, chunks))
        with patch('promotions.views._stream_ollama_response_by_target', return_value=provider) as mock:
            response = self.client.post(reverse('promotion_stream'), {
                'promotion_text': '模拟问题', 'model_select': 'model:synthetic',
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(QATiming.objects.get().status, 'pending')
            body = b''.join(response.streaming_content).decode()
            response.close()
            mock.assert_called_once()
        return body, QATiming.objects.get()

    def test_stream_success_and_session_scoped_idempotent_browser_report(self):
        body, timing = self.stream([
            {'message': {'content': '<think>hidden'}, 'done': False},
            {'message': {'content': '</think>visible'}, 'done': False},
            {'message': {}, 'done': True},
        ])
        self.assertEqual(body.count('event: delta'), 1)
        self.assertIn(str(timing.pk), body)
        self.assertEqual(timing.status, 'success')
        self.assertIsNotNone(timing.qa_record_id)
        self.assertLessEqual(timing.first_answer_ms, timing.backend_ms)
        self.assertIsNone(timing.client_total_ms)

        url = reverse('qa_timing_report')
        payload = {'timing_id': str(timing.pk), 'first_ms': 25, 'total_ms': 80}
        other_session = Client()
        other_session.force_login(self.user)
        self.assertEqual(other_session.post(url, payload, content_type='application/json').status_code, 404)
        self.assertEqual(self.client.post(url, {**payload, 'total_ms': 10}, content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post(url, payload, content_type='application/json').status_code, 200)
        self.assertEqual(self.client.post(url, {**payload, 'total_ms': 999}, content_type='application/json').status_code, 200)
        timing.refresh_from_db()
        self.assertEqual((timing.client_first_ms, timing.client_total_ms), (25, 80))

    def test_timeout_is_not_success(self):
        body, timing = self.stream(error=ReadTimeout('synthetic timeout'))
        self.assertIn('"result": "timeout"', body)
        self.assertEqual(timing.status, 'timeout')
        self.assertIsNone(timing.first_answer_ms)

    def test_truncated_model_stream_is_interrupted(self):
        _, timing = self.stream([{'message': {'content': 'partial'}, 'done': False}])
        self.assertEqual(timing.status, 'interrupted')

    def test_close_before_consumption_and_cancel_race(self):
        response = self.client.post(reverse('promotion_stream'), {
            'promotion_text': '取消模拟', 'model_select': 'model:synthetic',
        })
        timing = QATiming.objects.get()
        response.close()
        timing.refresh_from_db()
        self.assertEqual(timing.status, 'interrupted')
        result = self.client.post(reverse('promotion_stop'), {'timing_id': str(timing.pk)}, content_type='application/json')
        self.assertEqual(result.status_code, 200)
        timing.refresh_from_db()
        self.assertEqual(timing.status, 'cancelled')

    def test_ajax_fallback_keeps_response_shape_and_records_server_timing(self):
        provider = MagicMock()
        provider.__enter__.return_value = provider
        provider.iter_lines.return_value = iter([json.dumps({'message': {'content': '测试回答'}, 'done': True})])
        with patch('promotions.views._stream_ollama_response_by_target', return_value=provider):
            response = self.client.post(reverse('promotion'), {
                'promotion_text': '模拟普通回答', 'model_select': 'model:synthetic',
            }, headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['chat_history'][-1]['response'], '测试回答')
        timing = QATiming.objects.get()
        self.assertEqual(timing.status, 'success')
        self.assertIsNotNone(timing.backend_ms)
        self.assertIsNone(timing.client_total_ms)
