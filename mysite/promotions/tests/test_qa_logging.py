"""问答落库与兜底提取（jieba + 领域词典）测试（纯 DB，不联网）。"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from promotions.models import Agent, Patient, QARecord
from promotions.qa_store import (
    classify_topics,
    extract_keywords_jieba,
    extract_record,
    log_qa_record,
)


class QALoggingHelperTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('nurse', password='x')
        self.agent = Agent.objects.create(
            name='测试智能体', slug='test-agent', ollama_model='qwen3:4b',
            agent_type='legacy',
        )

    def test_log_qa_record_creates_row_with_all_fields(self):
        patient = Patient.objects.create(bed_no='1', name='张三', disease_type='parkinson')
        record = log_qa_record(
            user=self.user, source='web', patient=patient,
            model_key='agent:test-agent', question='帕金森术后多久可以程控？',
            answer='一般术后 2-4 周开机程控。', thinking='思考中……',
            latency_ms=1234, session_scope='user_1',
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.agent, self.agent)
        self.assertEqual(record.patient, patient)
        self.assertEqual(record.extraction_status, 'pending')
        self.assertEqual(record.latency_ms, 1234)
        self.assertEqual(record.source, 'web')

    def test_empty_question_and_answer_not_logged(self):
        record = log_qa_record(user=self.user, source='web',
                               question='   ', answer='  ')
        self.assertIsNone(record)
        self.assertEqual(QARecord.objects.count(), 0)

    def test_error_record_still_logged_with_error_field(self):
        record = log_qa_record(
            user=self.user, source='web', question='术后怎么护理？',
            answer='（服务端错误）无法连接 Ollama', error='ConnectionError: x',
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.error, 'ConnectionError: x')

    def test_separate_requests_with_repeated_text_are_counted(self):
        log_qa_record(user=self.user, source='web', question='重复问题',
                      answer='答案', session_scope='user_1')
        dup = log_qa_record(user=self.user, source='web', question='重复问题',
                            answer='答案', session_scope='user_1')
        self.assertIsNotNone(dup)
        self.assertEqual(QARecord.objects.count(), 2)

    def test_successful_retry_and_other_patients_are_not_dropped(self):
        first = Patient.objects.create(bed_no='A', name='合成甲', disease_type='parkinson')
        second = Patient.objects.create(bed_no='B', name='合成乙', disease_type='epilepsy')
        for patient, model, error in ((first, 'model:a', 'timeout'), (first, 'model:a', ''),
                                      (second, 'model:a', ''), (second, 'model:b', '')):
            self.assertIsNotNone(log_qa_record(
                user=self.user, patient=patient, model_key=model, error=error,
                question='同一问题', answer='' if error else '完整答复', session_scope='user_1',
            ))
        self.assertEqual(QARecord.objects.count(), 4)
        self.assertEqual(QARecord.objects.filter(error='').count(), 3)

    def test_same_question_after_90s_logged(self):
        first = log_qa_record(user=self.user, source='web', question='重复问题',
                              answer='答案', session_scope='user_1')
        QARecord.objects.filter(id=first.id).update(
            created_at=timezone.now() - timedelta(seconds=120))
        second = log_qa_record(user=self.user, source='web', question='重复问题',
                               answer='答案', session_scope='user_1')
        self.assertIsNotNone(second)
        self.assertEqual(QARecord.objects.count(), 2)

    def test_different_scope_not_duplicate(self):
        log_qa_record(user=self.user, source='web', question='重复问题',
                      answer='答案', session_scope='user_1')
        other = log_qa_record(user=self.user, source='web', question='重复问题',
                              answer='答案', session_scope='user_2')
        self.assertIsNotNone(other)

    def test_scope_resolution_from_user(self):
        # request=None 且 session_scope 为空时不会推断；此处验证显式 scope 原样保留
        record = log_qa_record(user=self.user, source='api',
                               question='q', answer='a', session_scope='user_1')
        self.assertEqual(record.session_scope, 'user_1')

    def test_classify_topics_maps_domain_keywords(self):
        self.assertIn('手术与DBS', classify_topics('帕金森术后多久可以程控开机'))
        self.assertIn('用药与副作用', classify_topics('左旋多巴需要停药吗'))
        self.assertIn('跌倒与安全', classify_topics('老人夜里上厕所怕摔倒怎么办'))
        self.assertEqual(['其他'], classify_topics('今天天气怎么样'))

    def test_extract_keywords_jieba_returns_domain_terms(self):
        words = extract_keywords_jieba('帕金森患者服用美多芭出现异动症怎么办', top_k=8)
        self.assertTrue(len(words) >= 2, f'keywords={words}')
        self.assertTrue(any('帕金森' in w or '美多芭' in w or '异动症' in w for w in words),
                        f'keywords={words}')

    def test_extract_record_fallback_without_ai_config(self):
        record = QARecord.objects.create(
            user=self.user, question='帕金森术后程控需要注意什么',
            answer='术后程控需定期复查。', extraction_status='pending',
        )
        outcome = extract_record(record.id)
        self.assertTrue(outcome['ok'])
        record.refresh_from_db()
        self.assertEqual(record.extraction_status, 'done')
        self.assertTrue(record.topics, 'topics should not be empty')
        self.assertTrue(record.keywords, 'keywords should not be empty')

    def test_extract_record_skips_already_processing(self):
        record = QARecord.objects.create(
            user=self.user, question='q', answer='a', extraction_status='processing',
        )
        outcome = extract_record(record.id)
        self.assertFalse(outcome['ok'])
        record.refresh_from_db()
        self.assertEqual(record.extraction_status, 'processing')
