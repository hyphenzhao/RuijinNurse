"""AI 提取路径与 JSON 解析测试（mock AI，不联网）。"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from promotions.ai_client import AIClientError, parse_model_json
from promotions.models import QARecord
from promotions.qa_store import extract_record


class ParseModelJsonTests(TestCase):
    def test_bare_json_dict(self):
        self.assertEqual(parse_model_json('{"a": 1}'), {'a': 1})

    def test_fenced_json_block(self):
        self.assertEqual(
            parse_model_json('```json\n{"keywords": ["a"]}\n```'),
            {'keywords': ['a']},
        )

    def test_prose_before_fenced_block(self):
        self.assertEqual(
            parse_model_json('好的，结果如下：\n```json\n{"topics": ["用药"]}\n```谢谢'),
            {'topics': ['用药']},
        )

    def test_array(self):
        self.assertEqual(parse_model_json('[1, 2, 3]'), [1, 2, 3])

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            parse_model_json('这里没有 JSON')

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            parse_model_json('')


class QaExtractionAiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('nurse', password='x')
        self.cfg = object()  # fake AIConfig，仅用于“有配置”分支

    def _make_record(self):
        return QARecord.objects.create(
            user=self.user, question='帕金森术后程控需要注意什么',
            answer='术后程控需定期复查。', extraction_status='pending',
        )

    @patch('promotions.qa_store.get_active_config', return_value=object())
    @patch('promotions.qa_store.chat_complete_json')
    def test_ai_extraction_writes_keywords_and_topics(self, mock_chat, mock_cfg):
        mock_chat.return_value = {'keywords': ['程控', '术后'], 'topics': ['手术与DBS']}
        record = self._make_record()
        outcome = extract_record(record.id)
        self.assertTrue(outcome['ok'])
        self.assertEqual(outcome['method'], 'ai')
        record.refresh_from_db()
        self.assertEqual(record.extraction_status, 'done')
        self.assertEqual(record.keywords, ['程控', '术后'])
        self.assertEqual(record.topics, ['手术与DBS'])

    @patch('promotions.qa_store.get_active_config', return_value=object())
    @patch('promotions.qa_store.chat_complete_json', return_value=None)
    def test_unparseable_ai_reply_falls_back(self, mock_chat, mock_cfg):
        record = self._make_record()
        outcome = extract_record(record.id)
        self.assertTrue(outcome['ok'])
        self.assertEqual(outcome['method'], 'fallback')
        record.refresh_from_db()
        self.assertEqual(record.extraction_status, 'done')
        self.assertTrue(record.extraction_error.startswith('ai_parse_failed'))
        self.assertTrue(record.topics)

    @patch('promotions.qa_store.get_active_config', return_value=object())
    @patch('promotions.qa_store.chat_complete_json',
           side_effect=AIClientError('连接 AI 服务失败'))
    def test_ai_error_falls_back_with_error_note(self, mock_chat, mock_cfg):
        record = self._make_record()
        outcome = extract_record(record.id)
        self.assertTrue(outcome['ok'])
        self.assertEqual(outcome['method'], 'fallback')
        record.refresh_from_db()
        self.assertEqual(record.extraction_status, 'done')
        self.assertIn('ai_failed', record.extraction_error)
        self.assertTrue(record.topics)

    @patch('promotions.qa_store.get_active_config', return_value=object())
    @patch('promotions.qa_store.chat_complete_json')
    def test_topics_not_in_whitelist_are_dropped(self, mock_chat, mock_cfg):
        mock_chat.return_value = {
            'keywords': ['程控'],
            'topics': ['手术与DBS', '不存在的主题', '用药与副作用'],
        }
        record = self._make_record()
        extract_record(record.id)
        record.refresh_from_db()
        self.assertEqual(record.topics, ['手术与DBS', '用药与副作用'])
