from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from promotions.models import Patient, QARecord, QATiming
from promotions.views import _build_insight_summary


class AnalyticsRegressionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('synthetic-analytics-review')
        self.client.force_login(self.user)
        self.patient = Patient.objects.create(name='模拟甲', bed_no='S1', disease_type='parkinson')
        self.other = Patient.objects.create(name='模拟乙', bed_no='S2', disease_type='epilepsy')
        for patient, question, error, topic in [
            (self.patient, '目标问题一', '', '手术与DBS'),
            (self.patient, '目标问题二', '', '手术与DBS'),
            (self.patient, '目标失败', 'timeout', '手术与DBS'),
            (self.patient, '其他失败', 'timeout', '用药与副作用'),
            (self.other, '目标失败', 'timeout', '手术与DBS'),
        ]:
            QARecord.objects.create(user=self.user, patient=patient, question=question,
                                    answer='模拟回答', error=error, topics=[topic])

    def test_invalid_patient_filter_returns_bad_request(self):
        for patient_id in ('not-a-patient', '1.5'):
            with self.subTest(patient_id=patient_id):
                response = self.client.get('/promotion/analytics/data/', {'patient_id': patient_id})
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.json()['ok'])

    def test_failure_count_uses_same_combined_filters(self):
        data = self.client.get('/promotion/analytics/data/', {
            'patient_id': self.patient.pk, 'disease': 'parkinson',
            'topic': '手术与DBS', 'q': '目标',
        }).json()['data']
        self.assertEqual(data['tiles']['total'], 2)
        self.assertEqual(data['tiles']['failed_count'], 1)

    def test_insight_counts_people_once(self):
        today = timezone.localdate()
        text = _build_insight_summary(today, today, QARecord.objects.filter(error=''))
        self.assertIn('帕金森病 1人', text)
        self.assertNotIn('2人', text)

    @patch('promotions.views.chat_complete_text', return_value='模拟分析')
    @patch('promotions.views.get_active_config')
    def test_insight_matches_keyword_and_patient_filters(self, config, chat):
        config.return_value = Mock(model='synthetic')
        response = self.client.post('/promotion/analytics/insight/', {
            'patient_id': self.patient.pk, 'q': '目标问题一',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        summary = chat.call_args.kwargs['user_text']
        self.assertIn('总问答数: 1', summary)
        self.assertIn('服务端失败记录: 0', summary)
        self.assertNotIn('目标问题二', summary)

    def test_app_source_filter_and_export(self):
        for source in ('web', 'api'):
            QATiming.objects.create(source=source, started_at=timezone.now(),
                                   status='success', backend_ms=100, model_name='synthetic')
        from django.urls import reverse
        response = self.client.get(reverse('qa_timing_analytics'), {'source': 'api'})
        data = response.json()['data']
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['records'][0]['source'], 'api')
        self.assertEqual(data['metrics']['client_total_ms']['count'], 0)
        export = self.client.get(reverse('qa_timing_export'), {'source': 'api'})
        body = b''.join(export.streaming_content).decode('utf-8-sig')
        self.assertIn('"api"', body)
        self.assertNotIn('"web"', body)
