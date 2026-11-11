"""App API 契约冒烟测试 — 确保二期升级不破坏移动端契约（不联网）。"""
import json
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from promotions.models import Agent, Patient, QARecord
from .serializers import ChatRequestSerializer

FAKE_SSE_EVENTS = [
    'event: status\ndata: {"message": "thinking"}\n\n',
    'event: delta\ndata: {"content": "回答"}\n\n',
    'event: done\ndata: {"final": "回答", "thinking": "", "audio_url": null}\n\n',
]


class ChatRequestSerializerTests(TestCase):
    def test_old_payload_remains_valid(self):
        serializer = ChatRequestSerializer(data={
            'text': '你好', 'model': 'agent:nurse-agent', 'session_id': 'abc',
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['text'], '你好')
        self.assertEqual(serializer.validated_data['session_id'], 'abc')
        self.assertNotIn('patient_id', serializer.validated_data)

    def test_new_payload_with_patient_id_is_valid(self):
        serializer = ChatRequestSerializer(data={
            'text': '你好', 'model': 'model:qwen3', 'patient_id': 3,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['patient_id'], 3)

    def test_unknown_extra_fields_are_ignored(self):
        serializer = ChatRequestSerializer(data={
            'text': '你好', 'model': 'model:qwen3', 'future_field': 'x',
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)


class ApiContractCompatibilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('nurse', password='test-pass-123')
        self.client = APIClient()
        login = self.client.post('/api/v1/auth/login/',
                                 {'username': 'nurse', 'password': 'test-pass-123'},
                                 format='json')
        self.assertEqual(login.status_code, 200, login.content)
        self.access = login.json()['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.access}')

    def test_login_returns_expected_shape(self):
        response = self.client.post('/api/v1/auth/login/',
                                    {'username': 'nurse', 'password': 'test-pass-123'},
                                    format='json')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn('access', payload)
        self.assertIn('refresh', payload)
        self.assertEqual(set(payload['user'].keys()), {'id', 'username', 'is_staff'})

    def test_login_wrong_password_401(self):
        response = self.client.post('/api/v1/auth/login/',
                                    {'username': 'nurse', 'password': 'wrong'},
                                    format='json')
        self.assertEqual(response.status_code, 401)

    def test_health_is_open(self):
        client = APIClient()  # 无凭据
        response = client.get('/api/v1/health/')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['version'], '1.0.0')

    def test_agents_list_shape(self):
        Agent.objects.create(name='护士智能体', slug='nurse-agent',
                             ollama_model='qwen3:4b', is_default=True)
        response = self.client.get('/api/v1/agents/')
        self.assertEqual(response.status_code, 200)
        items = response.json()
        self.assertEqual(len(items), 1)
        self.assertEqual(set(items[0].keys()), {
            'id', 'name', 'slug', 'ollama_host', 'ollama_port', 'ollama_model',
            'system_prompt', 'knowledge', 'is_active', 'agent_type', 'is_default',
        })

    def test_agents_requires_auth(self):
        client = APIClient()
        response = client.get('/api/v1/agents/')
        self.assertEqual(response.status_code, 401)

    def test_sessions_list_shape(self):
        response = self.client.get('/api/v1/sessions/')
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json(), list)

    @patch('api.chat_stream.requests.post')
    def test_chat_stream_wiring_logs_qa_record(self, mock_stream):
        patient = Patient.objects.create(bed_no='1', name='合成患者', disease_type='parkinson')
        mock_stream.return_value.iter_lines.return_value = [
            json.dumps({'message': {'content': '回答', 'thinking': '思考'}, 'done': False}),
            json.dumps({'message': {}, 'done': True}),
        ]
        with TemporaryDirectory() as folder, patch('api.views.RUNTIME_SESSIONS_DIR', Path(folder)):
            response = self.client.post('/api/v1/chat/stream/', {
                'text': '术后注意什么', 'model': 'model:qwen3', 'patient_id': patient.id,
            }, format='json')
            self.assertEqual(response.status_code, 200)
            body = b''.join(response.streaming_content).decode('utf-8')
        for name in ('status', 'delta', 'done'):
            self.assertIn(f'event: {name}', body)
        record = QARecord.objects.get(question='术后注意什么')
        self.assertEqual(record.source, 'api')
        self.assertEqual(record.user, self.user)
        self.assertEqual(record.patient, patient)
        self.assertEqual(record.session_scope, f'user_{self.user.id}')
        self.assertEqual(record.answer, '回答')
        self.assertEqual(record.thinking, '思考')

    def test_chat_stream_requires_auth(self):
        client = APIClient()
        response = client.post('/api/v1/chat/stream/',
                               {'text': 'hi', 'model': 'model:qwen3'}, format='json')
        self.assertEqual(response.status_code, 401)
