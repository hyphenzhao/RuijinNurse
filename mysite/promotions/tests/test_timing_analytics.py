import csv
import io
from datetime import timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from promotions.models import QARecord, QATiming


class TimingAnalyticsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('timing-analyst')
        self.client.force_login(self.user)
        self.data_url = reverse('qa_timing_analytics')
        self.export_url = reverse('qa_timing_export')

    def make_timing(self, **fields):
        now = timezone.now()
        defaults = {
            'request_id': uuid4(), 'source': 'web', 'model_key': 'agent:test',
            'model_name': 'model-a', 'started_at': now, 'finished_at': now,
            'status': 'success', 'backend_ms': 1000,
        }
        return QATiming.objects.create(**(defaults | fields))

    def test_success_metrics_keep_missing_and_failure_samples_separate(self):
        self.make_timing(client_total_ms=1200, client_first_ms=400)
        self.make_timing(client_total_ms=2800, client_first_ms=800)
        self.make_timing(client_total_ms=None)
        self.make_timing(status='timeout', client_total_ms=10000)
        self.make_timing(status='cancelled')
        self.make_timing(status='interrupted')
        self.make_timing(model_name='model-b', client_total_ms=8000)
        data = self.client.get(self.data_url, {'model': 'model-a'}).json()['data']
        self.assertEqual(data['total'], 6)
        self.assertEqual(data['failure_rate'], 0.25)
        self.assertEqual(data['metrics']['client_total_ms'], {
            'count': 2, 'average': 2000, 'median': 2000, 'p95': 2800,
        })
        self.assertEqual(data['metrics']['backend_ms']['count'], 3)
        self.assertEqual(data['trend'][-1]['client_median'], 2000)
        self.assertEqual(data['trend'][-1]['client_p95'], 2800)
        self.assertIsNone(data['trend'][0]['client_median'])

    def test_historical_qa_record_does_not_create_a_timing_sample(self):
        QARecord.objects.create(user=self.user, question='synthetic historical query', latency_ms=1500)
        data = self.client.get(self.data_url).json()['data']
        self.assertEqual(data['total'], 0)
        self.assertIsNone(data['metrics']['client_total_ms']['average'])

    def test_export_only_includes_timing_and_quotes_formula_model_name(self):
        question = QARecord.objects.create(user=self.user, question='synthetic private field')
        rec = self.make_timing(model_name='=1+1', qa_record=question, client_total_ms=1800)
        self.make_timing(model_name='other')
        response = self.client.get(self.export_url, {'model': '=1+1'})
        self.assertEqual(response.status_code, 200)
        result = b''.join(response.streaming_content).decode('utf-8-sig')
        self.assertNotIn(question.question, result)
        rows = list(csv.reader(io.StringIO(result)))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], str(rec.request_id))
        self.assertEqual(rows[1][4], "'=1+1")

    def test_date_window_and_authenticated_access(self):
        day = timezone.localdate()
        self.make_timing(started_at=timezone.now() - timedelta(days=2))
        data = self.client.get(self.data_url, {'start': day.isoformat(), 'end': day.isoformat()}).json()['data']
        self.assertEqual(data['total'], 0)
        self.assertEqual(self.client.get(self.data_url, {'start': '1900-01-01'}).status_code, 400)
        self.client.logout()
        self.assertEqual(self.client.get(self.data_url).status_code, 302)
        self.assertEqual(self.client.get(self.export_url).status_code, 302)
