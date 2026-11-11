"""数据分析页与统计端点测试（造数 + mock AI，不联网）。"""
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from promotions.models import AIConfig, Patient, QARecord


class AnalyticsViewsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('nurse', password='x')
        self.client.login(username='nurse', password='x')
        today = timezone.localdate()
        self.patient_pd = Patient.objects.create(bed_no='1', name='张三',
                                                 disease_type='parkinson')
        self.patient_ep = Patient.objects.create(bed_no='2', name='李四',
                                                 disease_type='epilepsy')
        # 3 天分布：昨天 2 条帕金森（已提取）、前天 1 条癫痫、今天 1 条 + 1 条失败 + 1 条 pending
        self._create(today - timedelta(days=1), self.patient_pd,
                     '帕金森术后多久可以程控', ['手术与DBS'], ['程控'], 'done')
        self._create(today - timedelta(days=1), self.patient_pd,
                     '美多芭需要停药吗', ['用药与副作用'], ['美多芭'], 'done')
        self._create(today - timedelta(days=2), self.patient_ep,
                     '癫痫发作怎么急救', ['癫痫发作与急救'], ['癫痫'], 'done')
        self._create(today, None, '今天的问题', [], [], 'pending')
        QARecord.objects.create(
            user=self.user, question='失败记录', answer='（服务端错误）超时',
            error='ConnectionError', extraction_status='pending',
        )

    def _create(self, day, patient, question, topics, keywords, status):
        rec = QARecord.objects.create(
            user=self.user, patient=patient, source='web', model_key='agent:x',
            question=question, answer='这是回答', topics=topics, keywords=keywords,
            extraction_status=status, latency_ms=1000,
        )
        # created_at 为 auto_now_add，直接改写以控制日期分布
        QARecord.objects.filter(id=rec.id).update(
            created_at=timezone.make_aware(
                timezone.datetime.combine(day, timezone.datetime.min.time()))
            + timedelta(hours=10)
        )
        return rec

    def test_page_renders_with_tabs_and_pending_count(self):
        response = self.client.get('/promotion/analytics/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '数据分析')
        self.assertContains(response, 'AI 驱动配置')
        self.assertContains(response, '系统管理')
        self.assertContains(response, '患者管理')

    def test_data_default_range_and_tiles(self):
        response = self.client.get('/promotion/analytics/data/')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        data = payload['data']
        # 4 条正常记录（失败记录被排除）
        self.assertEqual(data['tiles']['total'], 4)
        self.assertEqual(data['tiles']['today'], 1)
        self.assertEqual(data['tiles']['patient_linked'], 3)
        self.assertEqual(data['tiles']['avg_latency_ms'], 1000)
        self.assertEqual(data['tiles']['pending_extraction'], 1)
        self.assertEqual(data['tiles']['failed_count'], 1)
        # 趋势补齐 30 天
        self.assertEqual(len(data['trend']), 30)
        self.assertEqual(sum(d['count'] for d in data['trend']), 4)

    def test_topic_and_keyword_aggregation(self):
        data = self.client.get('/promotion/analytics/data/').json()['data']
        topic_counts = {t['name']: t['count'] for t in data['topics']}
        self.assertEqual(topic_counts['手术与DBS'], 1)
        self.assertEqual(topic_counts['用药与副作用'], 1)
        self.assertEqual(topic_counts['癫痫发作与急救'], 1)
        keyword_counts = {k['name']: k['count'] for k in data['keywords']}
        self.assertEqual(keyword_counts['程控'], 1)

    def test_top_questions_aggregation(self):
        data = self.client.get('/promotion/analytics/data/').json()['data']
        questions = [item['question'] for item in data['top_questions']]
        # 各问题 count 均为 1，按最近提问时间排序 → 今天的问题排第一
        self.assertEqual(questions[0], '今天的问题')
        counts = {item['question']: item['count'] for item in data['top_questions']}
        self.assertEqual(counts['帕金森术后多久可以程控'], 1)
        self.assertEqual(len(questions), 4)

    def test_filters_narrow_results(self):
        data = self.client.get('/promotion/analytics/data/',
                               {'patient_id': self.patient_pd.id}).json()['data']
        self.assertEqual(data['tiles']['total'], 2)
        data = self.client.get('/promotion/analytics/data/',
                               {'disease': 'epilepsy'}).json()['data']
        self.assertEqual(data['tiles']['total'], 1)
        data = self.client.get('/promotion/analytics/data/',
                               {'topic': '手术与DBS'}).json()['data']
        self.assertEqual(data['tiles']['total'], 1)
        data = self.client.get('/promotion/analytics/data/',
                               {'q': '程控'}).json()['data']
        self.assertEqual(data['tiles']['total'], 1)

    def test_date_range_filters(self):
        today = timezone.localdate()
        data = self.client.get('/promotion/analytics/data/', {
            'start': (today - timedelta(days=1)).isoformat(),
            'end': today.isoformat(),
        }).json()['data']
        self.assertEqual(data['tiles']['total'], 3)  # 昨天 2 + 今天 1（失败已排除）

    def test_records_pagination(self):
        data = self.client.get('/promotion/analytics/data/',
                               {'page': 1, 'page_size': 2}).json()['data']
        self.assertEqual(len(data['records']), 2)
        self.assertEqual(data['pagination']['total'], 4)
        data2 = self.client.get('/promotion/analytics/data/',
                                {'page': 2, 'page_size': 2}).json()['data']
        self.assertEqual(len(data2['records']), 2)

    def test_extract_endpoint_processes_pending(self):
        response = self.client.post('/promotion/analytics/extract/')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        # 1 条 pending（失败记录也是 pending，会被处理）→ done ≥ 1
        self.assertEqual(payload['done'] + payload['failed'], 2)
        self.assertEqual(QARecord.objects.filter(extraction_status='pending').count(), 0)
        today_record = QARecord.objects.get(question='今天的问题')
        self.assertEqual(today_record.extraction_status, 'done')
        self.assertTrue(today_record.keywords or today_record.topics)

    def test_insight_without_config_returns_400(self):
        response = self.client.post('/promotion/analytics/insight/', data={},
                                    content_type='application/json')
        self.assertEqual(response.status_code, 400)

    @patch('promotions.views.chat_complete_text')
    @patch('promotions.views.get_active_config')
    def test_insight_with_config_returns_text(self, mock_cfg, mock_chat):
        cfg = AIConfig.objects.create(provider_type='ollama',
                                      base_url='http://127.0.0.1:11434',
                                      model='qwen3:4b', is_active=True)
        mock_cfg.return_value = cfg
        mock_chat.return_value = '## 总体概况\n测试简报内容'
        response = self.client.post('/promotion/analytics/insight/', data=json.dumps({}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertIn('总体概况', payload['text'])
        self.assertEqual(payload['model'], 'qwen3:4b')

    def test_ai_fetch_models_endpoint_bad_provider(self):
        response = self.client.post('/promotion/analytics/ai/fetch-models/',
                                    data=json.dumps({'provider_type': 'nope'}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 400)

    @patch('promotions.views.fetch_models', return_value=(True, ['m1', 'm2']))
    def test_ai_fetch_models_endpoint_ok(self, mock_fetch):
        response = self.client.post('/promotion/analytics/ai/fetch-models/',
                                    data=json.dumps({
                                        'provider_type': 'ollama',
                                        'base_url': 'http://127.0.0.1:11434',
                                    }),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['models'], ['m1', 'm2'])

    def test_ai_save_config_activates_single_row(self):
        AIConfig.objects.create(provider_type='ollama',
                                base_url='http://old', model='old', is_active=True)
        response = self.client.post('/promotion/analytics/ai/save-config/',
                                    data=json.dumps({
                                        'provider_type': 'deepseek',
                                        'base_url': 'https://api.deepseek.com/v1',
                                        'api_key': 'sk-test',
                                        'model': 'deepseek-chat',
                                    }),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        active = AIConfig.objects.filter(is_active=True)
        self.assertEqual(active.count(), 1)
        self.assertEqual(active.first().provider_type, 'deepseek')
