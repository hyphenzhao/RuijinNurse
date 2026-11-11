"""聊天流挂接测试：患者关联落库 + 患者信息注入系统上下文（mock Ollama 流）。"""
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from promotions.models import Agent, Patient, QARecord, SurgeryPlan

FAKE_LINES = [
    json.dumps({'message': {'content': '好的，已了解。'}, 'done': False}),
    json.dumps({'message': {}, 'done': True}),
]


class FakeStreamResponse:
    """模拟 requests 流式响应：上下文管理器 + raise_for_status + iter_lines"""
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=True):
        for line in FAKE_LINES:
            yield line


class ChatPatientContextTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('nurse', password='x')
        self.client.login(username='nurse', password='x')
        self.agent = Agent.objects.create(
            name='护士智能体', slug='nurse-agent', ollama_model='qwen3:4b',
            agent_type='legacy',
        )
        self.patient = Patient.objects.create(
            bed_no='12', name='张伟', gender='male', birth_date=timezone.localdate().replace(year=1960),
            disease_type='parkinson', admission_time=timezone.now() - timedelta(days=2),
            cautions='跌倒风险高',
        )
        SurgeryPlan.objects.create(
            patient=self.patient,
            planned_time=timezone.now() + timedelta(days=5),
            note='DBS 植入',
        )

    def _select_patient(self, patient_id):
        return self.client.post(
            '/promotion/patient/select/',
            data=json.dumps({'patient_id': patient_id}),
            content_type='application/json',
        )

    def _post_stream(self, question='这位患者 DBS 术后注意什么？'):
        return self.client.post('/promotion/stream/', {
            'promotion_text': question,
            'model_select': 'agent:nurse-agent',
        })

    @patch('promotions.views._stream_ollama_response_by_target')
    def test_stream_with_patient_logs_record_and_injects_context(self, mock_stream):
        mock_stream.side_effect = lambda base_url, model_name, messages: FakeStreamResponse(
            {'messages': messages})
        self._select_patient(self.patient.id)

        response = self._post_stream()
        self.assertEqual(response.status_code, 200)
        body = b''.join(response.streaming_content)
        text = body.decode('utf-8')
        self.assertIn('event: done', text)
        self.assertIn('好的，已了解。', text)

        # 问答入库：关联患者正确
        records = QARecord.objects.filter(question='这位患者 DBS 术后注意什么？')
        self.assertEqual(records.count(), 1)
        record = records.first()
        self.assertEqual(record.patient, self.patient)
        self.assertEqual(record.source, 'web')
        self.assertEqual(record.user, self.user)
        self.assertIn('好的，已了解。', record.answer)
        self.assertEqual(record.error, '')

        # 患者信息注入系统上下文
        sent_messages = mock_stream.call_args[0][2]
        system_content = sent_messages[0]['content']
        self.assertIn('[患者信息]', system_content)
        self.assertIn('张伟', system_content)
        self.assertIn('DBS 植入', system_content)
        self.assertIn('跌倒风险高', system_content)

    @patch('promotions.views._stream_ollama_response_by_target')
    def test_stream_without_patient_has_no_patient_block(self, mock_stream):
        mock_stream.side_effect = lambda base_url, model_name, messages: FakeStreamResponse(
            {'messages': messages})
        self._select_patient('')  # 清除关联

        response = self._post_stream('普通问题？')
        self.assertEqual(response.status_code, 200)
        # 流式响应是惰性生成器，必须消费后才完成落库
        b''.join(response.streaming_content)

        record = QARecord.objects.get(question='普通问题？')
        self.assertIsNone(record.patient)
        sent_messages = mock_stream.call_args[0][2]
        system_content = sent_messages[0]['content']
        self.assertNotIn('[患者信息]', system_content)

    def test_patient_select_and_clear(self):
        response = self._select_patient(self.patient.id)
        self.assertTrue(response.json()['ok'])
        self.assertEqual(self.client.session['current_patient_id'], self.patient.id)

        response = self._select_patient('')
        self.assertTrue(response.json()['ok'])
        self.assertNotIn('current_patient_id', self.client.session)

    def test_patient_select_unknown_returns_404(self):
        response = self._select_patient(99999)
        self.assertEqual(response.status_code, 404)

    @patch('promotions.views._stream_ollama_response_by_target')
    def test_stream_error_still_logs_with_error_field(self, mock_stream):
        mock_stream.side_effect = ConnectionError('boom')
        response = self._post_stream('会失败的问题')
        self.assertEqual(response.status_code, 200)
        body = b''.join(response.streaming_content).decode('utf-8')
        self.assertIn('服务端错误', body)
        record = QARecord.objects.get(question='会失败的问题')
        self.assertTrue(record.error)
        self.assertIn('ConnectionError', record.error)
