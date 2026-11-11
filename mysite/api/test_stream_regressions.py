"""Synthetic API regressions: privacy boundaries, stream outcomes, timing."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from promotions.models import Patient, QARecord, QATiming
from . import views


class FakeResponse:
    def __init__(self, frames):
        self.frames = frames
        self.closed = False
        self.raw = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def raise_for_status(self):
        pass

    def iter_lines(self, **kwargs):
        for frame in self.frames:
            if isinstance(frame, BaseException):
                raise frame
            yield json.dumps(frame)

    def close(self):
        self.closed = True


def answer_frames(answer='合成回答'):
    return [{'message': {'content': answer}, 'done': False}, {'message': {}, 'done': True}]


class ApiStreamRegressionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('synthetic', password='x')
        self.other = User.objects.create_user('synthetic-other', password='x')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path_patch = patch('api.views.RUNTIME_SESSIONS_DIR', Path(self.folder.name))
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.patient_a = Patient.objects.create(bed_no='A', name='合成甲', disease_type='parkinson')
        self.patient_b = Patient.objects.create(bed_no='B', name='合成乙', disease_type='epilepsy')

    def chat(self, frames=None, **data):
        payload = {'text': '合成问题', 'model': 'model:test', **data}
        response_mock = FakeResponse(answer_frames() if frames is None else frames)
        with patch('api.chat_stream.requests.post', return_value=response_mock) as upstream:
            response = self.client.post('/api/v1/chat/stream/', payload, format='json')
            body = b''.join(response.streaming_content).decode() if response.streaming else response.content.decode()
        return response, body, upstream, response_mock

    def test_backend_timing_is_saved_without_app_update(self):
        response, body, _, upstream = self.chat()
        self.assertEqual(response.status_code, 200)
        row = QATiming.objects.get()
        self.assertEqual(row.source, 'api')
        self.assertEqual(row.status, 'success')
        self.assertIsNotNone(row.backend_ms)
        self.assertIsNotNone(row.first_answer_ms)
        self.assertIsNone(row.client_total_ms)
        self.assertEqual(row.qa_record.source, 'api')
        self.assertIn(str(row.pk), body)
        self.assertEqual(response['X-QA-Timing-ID'], str(row.pk))
        self.assertTrue(upstream.closed)

    def test_patient_context_isolated_and_resumable(self):
        self.chat(text='甲的问题', patient_id=self.patient_a.pk)
        _, _, upstream_b, _ = self.chat(text='乙的问题', patient_id=self.patient_b.pk)
        contents_b = [m['content'] for m in upstream_b.call_args.kwargs['json']['messages']]
        self.assertNotIn('甲的问题', contents_b)
        _, _, upstream_a, _ = self.chat(text='甲的后续', patient_id=self.patient_a.pk)
        contents_a = [m['content'] for m in upstream_a.call_args.kwargs['json']['messages']]
        self.assertIn('甲的问题', contents_a)
        self.assertNotIn('乙的问题', contents_a)
        _, _, upstream_none, _ = self.chat(text='通用问题')
        self.assertEqual([m['content'] for m in upstream_none.call_args.kwargs['json']['messages'] if m['role'] != 'system'], ['通用问题'])

    def test_legacy_unscoped_history_is_preserved_but_never_loaded(self):
        legacy = Path(self.folder.name) / f'user_{self.user.pk}__model_test.json'
        original = json.dumps({'messages': [{'role': 'assistant', 'content': '旧患者信息'}]})
        legacy.write_text(original)
        _, _, upstream, _ = self.chat()
        self.assertNotIn('旧患者信息', str(upstream.call_args.kwargs['json']))
        self.assertEqual(legacy.read_text(), original)

    def test_truncated_empty_and_timeout_answers_not_used_as_history(self):
        cases = [([{'message': {'content': '截断内容'}, 'done': False}], 'interrupted'),
                 ([{'message': {'thinking': '仅思考'}, 'done': True}], 'failed'),
                 ([requests.exceptions.ReadTimeout('synthetic')], 'timeout')]
        for frames, expected in cases:
            with self.subTest(status=expected):
                _, body, _, _ = self.chat(frames=frames)
                row = QATiming.objects.latest('started_at')
                self.assertEqual(row.status, expected)
                self.assertTrue(row.qa_record.error)
                self.assertIn('event: error', body)
                self.assertIn('回答未完成', body)  # readable by old clients that ignore result
                self.assertFalse(list(Path(self.folder.name).glob('*.json')))
        _, _, upstream, _ = self.chat(text='新的问题')
        messages = upstream.call_args.kwargs['json']['messages']
        self.assertEqual([m['content'] for m in messages if m['role'] != 'system'], ['新的问题'])

    def test_invalid_patient_does_not_fall_back_to_general_chat(self):
        response = self.client.post('/api/v1/chat/stream/', {'text': 'q', 'model': 'model:test', 'patient_id': 9999}, format='json')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(QATiming.objects.count(), 0)

    def test_app_timing_report_is_owner_scoped_and_idempotent(self):
        self.chat()
        row = QATiming.objects.get()
        data = {'timing_id': str(row.pk), 'first_ms': 42, 'total_ms': 90}
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post('/api/v1/chat/timing/', data, format='json').status_code, 404)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post('/api/v1/chat/timing/', data, format='json').status_code, 200)
        data['total_ms'] = 300
        self.assertEqual(self.client.post('/api/v1/chat/timing/', data, format='json').status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.client_total_ms, 90)
        data['first_ms'] = True
        self.assertEqual(self.client.post('/api/v1/chat/timing/', data, format='json').status_code, 400)

    def test_stop_cannot_cancel_another_users_request(self):
        response = self.client.post('/api/v1/chat/stream/', {'text': 'q', 'model': 'model:test'}, format='json')
        row = QATiming.objects.get()
        self.client.force_authenticate(self.other)
        self.client.post('/api/v1/chat/stop/', {'timing_id': str(row.pk)}, format='json')
        row.refresh_from_db()
        self.assertEqual(row.status, 'pending')
        self.client.force_authenticate(self.user)
        self.client.post('/api/v1/chat/stop/', {'timing_id': str(row.pk)}, format='json')
        with patch('api.chat_stream.requests.post') as upstream:
            b''.join(response.streaming_content)
        self.assertFalse(upstream.called)
        row.refresh_from_db()
        self.assertEqual(row.status, 'cancelled')
        self.assertIsNotNone(row.qa_record_id)

    def test_preparation_failure_finishes_timing(self):
        with patch('api.views.load_role_and_knowledge', side_effect=OSError('synthetic')):
            response = self.client.post('/api/v1/chat/stream/', {'text': 'q', 'model': 'model:test'}, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(QATiming.objects.get().status, 'failed')

    def test_legacy_stop_is_scoped_to_current_api_user(self):
        first = self.client.post('/api/v1/chat/stream/', {'text': 'one', 'model': 'model:test'}, format='json')
        first_id = first['X-QA-Timing-ID']
        self.client.force_authenticate(self.other)
        second = self.client.post('/api/v1/chat/stream/', {'text': 'two', 'model': 'model:test'}, format='json')
        second_id = second['X-QA-Timing-ID']
        self.client.force_authenticate(self.user)
        self.client.post('/api/v1/chat/stop/', {}, format='json')
        self.assertEqual(QATiming.objects.get(pk=first_id).status, 'cancelled')
        self.assertEqual(QATiming.objects.get(pk=second_id).status, 'pending')
        first.close()
        second.close()

    def test_disconnect_is_recorded_and_closes_upstream(self):
        upstream = FakeResponse(answer_frames())
        with patch('api.chat_stream.requests.post', return_value=upstream):
            response = self.client.post('/api/v1/chat/stream/', {'text': 'q', 'model': 'model:test'}, format='json')
            iterator = iter(response.streaming_content)
            next(iterator)  # status
            next(iterator)  # first answer
            response.close()
        self.assertTrue(upstream.closed)
        self.assertEqual(QATiming.objects.get().status, 'interrupted')
        self.assertFalse(list(Path(self.folder.name).glob('*.json')))


class SessionAuthorizationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(id=1, username='synthetic-owner')
        self.other = User.objects.create_user(id=10, username='synthetic-other')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        patcher = patch('api.views.RUNTIME_SESSIONS_DIR', Path(self.folder.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_prefix_neighbor_cannot_be_read_or_deleted(self):
        path = Path(self.folder.name) / 'user_10__model_test.json'
        path.write_text(json.dumps({'messages': [], 'model_key': 'model:test'}))
        for verb in (self.client.get, self.client.delete):
            self.assertEqual(verb('/api/v1/sessions/user_10__model_test/').status_code, 403)
        self.assertTrue(path.exists())

    def test_own_session_and_legacy_history_remain_accessible(self):
        path = Path(self.folder.name) / 'user_1__model_test.json'
        path.write_text(json.dumps({'messages': [], 'model_key': 'model:test'}))
        self.assertEqual(self.client.get('/api/v1/sessions/user_1__model_test/').status_code, 200)
        self.assertEqual(self.client.delete('/api/v1/sessions/user_1__model_test/').status_code, 200)
        self.assertFalse(path.exists())

    def test_windows_path_separators_are_rejected(self):
        self.assertFalse(views._owns_session(1, 'user_1__..\\user_10__model'))
        self.assertFalse(views._owns_session(1, 'user_1__../user_10__model'))
