"""
Q&A record persistence and keyword/topic extraction.

- log_qa_record(): called at the three chat write-sites (web stream, legacy
  AJAX, mobile API).  Never raises — logging must not break the chat flow.
- extract_record(): synchronous extraction used by the manual "提取未处理"
  batch button on the analytics page.  AI-assisted when an active AIConfig
  exists, otherwise jieba + domain-dictionary fallback.  Always converges to
  extraction_status='done' (with extraction_error noted when the AI failed).
"""
import re

from django.utils import timezone

from .ai_client import AIClientError, chat_complete_json, get_active_config
from .models import Agent, AIConfig, Patient, QARecord

# ──────────────────────────────────────────────────────────────────────────
#  领域主题词典（功能神经外科病区）
# ──────────────────────────────────────────────────────────────────────────

TOPIC_BUCKETS = {
    '手术与DBS': ['DBS', '脑起搏', '脑起搏器', '电极', '程控', '开机', '关机', '充电', '手术',
               '术后', '开颅', '植入', '毁损', '术式', '麻醉', '术前', '手术时间'],
    '用药与副作用': ['药', '停药', '减药', '调药', '吃药', '服药', '左旋多巴', '美多芭', '司来吉兰',
                '普拉克索', '苯妥英', '丙戊酸', '卡马西平', '奥卡西平', '氯硝西泮', '副作用',
                '不良反应', '剂量', '漏服', '不良反应'],
    '癫痫发作与急救': ['癫痫', '发作', '抽搐', '大发作', '小发作', '先兆', '失神', '急救',
                 '咬舌', '发作频率', '持续状态'],
    '出院与随访': ['出院', '复诊', '复查', '随访', '拆线', '换药', '伤口', '出院手续', '出院后'],
    '康复与锻炼': ['康复', '锻炼', '训练', '走路', '步态', '平衡', '肌张力', '僵硬', '震颤',
               '手抖', '康复训练', '物理治疗', '作业治疗'],
    '饮食与营养': ['饮食', '吃', '喝', '营养', '吞咽', '呛咳', '呛', '流食', '忌口', '饮水',
               '便秘', '补钙', '蛋白'],
    '跌倒与安全': ['跌倒', '摔倒', '滑倒', '防滑', '床栏', '护栏', '走失', '防走失', '安全',
               '坠床', '磕碰'],
    '心理与情绪': ['失眠', '睡眠', '焦虑', '抑郁', '情绪', '心情', '烦躁', '幻觉', '妄想',
               '精神', '记忆力', '认知', '老年痴呆'],
    '费用与医保': ['医保', '费用', '报销', '押金', '住院费', '手术费', '报销比例', '异地就医',
               '自费', '多少钱', '价格'],
    '住院手续': ['住院', '入院', '床位', '病房', '入院手续', '住院押金', '探视', '陪护',
               '家属', '陪床', '住院天数', '住院时间'],
    '检查与化验': ['检查', '化验', '磁共振', '核磁', 'CT', '脑电', '脑电图', '心电图', '抽血',
               'B超', '影像', '评估', '量表'],
}

# 医学专有词：注册进 jieba，保证分词正确（jieba 按需惰性加载）
MEDICAL_TERMS = [
    '脑起搏器', '脑起搏', '左旋多巴', '美多芭', '司来吉兰', '普拉克索', '苯妥英',
    '丙戊酸', '卡马西平', '奥卡西平', '氯硝西泮', '肌张力障碍', '帕金森病', '帕金森',
    '精神分裂症', '情感障碍', '癫痫发作', '脑电图', '核磁共振', '磁共振', '程控',
    '开机', '关机', '充电', '颤冰', '冻结步态', '剂末现象', '开关现象', '异动症',
]

# 主题白名单（与提取 Prompt 保持一字不差，用于校验 AI 返回的主题）
TOPIC_WHITELIST = [
    '手术与DBS', '用药与副作用', '癫痫发作与急救', '出院与随访', '康复与锻炼',
    '饮食与营养', '跌倒与安全', '心理与情绪', '费用与医保', '住院手续', '检查与化验', '其他',
]

EXTRACT_SYSTEM_PROMPT = (
    '你是医疗问答分析助手，只输出 JSON，不要 markdown 代码块，不要多余解释。'
)

EXTRACT_USER_TEMPLATE = (
    '请分析以下【患者问题】与【AI回答】，输出 JSON：\n'
    '{{"keywords": ["3-6个关键词，中文短语"], '
    '"topics": ["主题，必须取自: 手术与DBS, 用药与副作用, 癫痫发作与急救, 出院与随访, '
    '康复与锻炼, 饮食与营养, 跌倒与安全, 心理与情绪, 费用与医保, 住院手续, 检查与化验, 其他"]}}\n\n'
    '【患者问题】\n{question}\n\n【AI回答】\n{answer}'
)

# ──────────────────────────────────────────────────────────────────────────
#  问答落库
# ──────────────────────────────────────────────────────────────────────────

def log_qa_record(*, request=None, source='web', user=None, patient=None,
                  model_key='', question='', answer='', thinking='',
                  latency_ms=None, session_scope='', error=''):
    """
    Persist one completed Q&A exchange.  Never raises.

    Each invocation represents one accepted request. Repeated text may be a
    legitimate retry or a different patient/model and must remain countable.
    """
    try:
        question = (question or '').strip()
        answer = (answer or '').strip()
        if not question and not answer:
            return None

        # resolve agent FK for agent:{slug} model keys
        agent = None
        if model_key and model_key.startswith('agent:'):
            try:
                agent = Agent.objects.filter(slug=model_key.split(':', 1)[1]).first()
            except Exception:
                agent = None

        # resolve session scope: explicit arg > request user > session key
        if not session_scope and request is not None:
            if getattr(request.user, 'is_authenticated', False):
                session_scope = f'user_{request.user.id}'
            elif request.session.session_key:
                session_scope = request.session.session_key

        record = QARecord.objects.create(
            user=user,
            patient=patient,
            agent=agent,
            source=source,
            session_scope=session_scope or '',
            model_key=model_key or '',
            question=question,
            answer=answer,
            thinking=thinking or '',
            error=error or '',
            keywords=[],
            topics=[],
            extraction_status='pending',
            extraction_error='',
            latency_ms=latency_ms,
        )
        return record
    except Exception:
        # Chat flow must never break because of logging failures.
        return None


# ──────────────────────────────────────────────────────────────────────────
#  关键词/主题提取（手动批量触发）
# ──────────────────────────────────────────────────────────────────────────

def extract_record(record_id):
    """
    Extract keywords/topics for one QARecord.  Returns a result dict:

    {'ok': True, 'method': 'ai'|'fallback', 'keywords': [...], 'topics': [...],
     'error': ''}

    'ok' is False only when the record does not exist or is already being
    processed; the caller keeps the extraction_status bookkeeping.
    """
    record = QARecord.objects.filter(id=record_id).first()
    if record is None:
        return {'ok': False, 'error': '记录不存在'}

    # Atomic claim: only the caller that flips pending->processing proceeds.
    claimed = QARecord.objects.filter(
        id=record_id, extraction_status__in=('pending', 'error')
    ).update(extraction_status='processing')
    if not claimed:
        return {'ok': False, 'error': '已在处理中'}

    keywords, topics, method, error_text = _do_extract(record)

    record.keywords = keywords
    record.topics = topics
    record.extraction_status = 'done'
    record.extraction_error = error_text
    record.save(update_fields=['keywords', 'topics', 'extraction_status', 'extraction_error'])
    return {'ok': True, 'method': method, 'keywords': keywords, 'topics': topics,
            'error': error_text}


def _do_extract(record):
    """AI first, fallback on failure.  Returns (keywords, topics, method, error_text)."""
    cfg = get_active_config()
    if cfg is not None:
        try:
            payload = chat_complete_json(
                cfg=cfg,
                system=EXTRACT_SYSTEM_PROMPT,
                user_text=EXTRACT_USER_TEMPLATE.format(
                    question=record.question,
                    answer=(record.answer or '')[:800],
                ),
            )
            if isinstance(payload, dict):
                keywords = _clean_keywords(payload.get('keywords'))
                topics = _clean_topics(payload.get('topics'))
                if keywords or topics:
                    return keywords, topics, 'ai', ''
            return (*_fallback(record), 'fallback', 'ai_parse_failed: AI 返回缺少有效字段')
        except AIClientError as e:
            return (*_fallback(record), 'fallback', f'ai_failed: {e}')
        except Exception as e:
            return (*_fallback(record), 'fallback', f'ai_failed: {type(e).__name__}: {e}')
    return (*_fallback(record), 'fallback', '')


def _fallback(record):
    """jieba keyword extraction + domain-dictionary topic classification."""
    text = f'{record.question}\n{(record.answer or "")[:500]}'
    keywords = extract_keywords_jieba(record.question, top_k=8)
    if not keywords:
        keywords = extract_keywords_jieba(text, top_k=8)
    topics = classify_topics(text)
    return keywords, topics


def _clean_keywords(raw):
    if not isinstance(raw, list):
        return []
    seen, out = set(), []
    for item in raw:
        word = str(item).strip()
        if not word or len(word) > 20 or word in seen:
            continue
        seen.add(word)
        out.append(word)
    return out[:10]


def _clean_topics(raw):
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        topic = str(item).strip()
        if topic in TOPIC_WHITELIST and topic not in out:
            out.append(topic)
    return out[:3]


def classify_topics(text):
    """Match the text against TOPIC_BUCKETS; returns matched topic names."""
    text = text or ''
    matched = []
    for topic, words in TOPIC_BUCKETS.items():
        if any(word in text for word in words):
            matched.append(topic)
    return matched or ['其他']


def extract_keywords_jieba(text, top_k=8):
    """jieba TF-IDF keywords, with medical terms registered for correct segmentation."""
    try:
        import jieba
        import jieba.analyse
    except ImportError:
        return _dictionary_keywords(text, top_k)
    try:
        for term in MEDICAL_TERMS:
            jieba.add_word(term)
        words = jieba.analyse.extract_tags(text or '', topK=top_k, withWeight=False)
        return [w for w in words if len(w) >= 2][:top_k]
    except Exception:
        return _dictionary_keywords(text, top_k)


def _dictionary_keywords(text, top_k=8):
    """Last-resort keyword extraction without jieba: domain terms + longer runs."""
    text = text or ''
    found = [term for term in MEDICAL_TERMS if term in text]
    # Chinese runs of 2-6 chars not already covered, as crude keywords
    for run in re.findall(r'[一-鿿]{2,6}', text):
        if run not in found and run not in ('请问', '医生', '护士', '什么', '怎么', '知道'):
            found.append(run)
    return found[:top_k]


# ──────────────────────────────────────────────────────────────────────────
#  患者信息上下文（提供给聊天 AI 做参考）
# ──────────────────────────────────────────────────────────────────────────

def format_patient_context(patient):
    """
    Build the '[患者信息]' text block injected into the chat system context.

    Returns '' when no patient is given.  Missing fields are skipped.
    """
    if patient is None:
        return ''
    lines = ['[患者信息]']
    if patient.bed_no:
        lines.append(f'床号: {patient.bed_no}')
    name_parts = [patient.name or '']
    if patient.gender:
        name_parts.append('男' if patient.gender == 'male' else '女')
    if patient.age is not None:
        name_parts.append(f'{patient.age}岁')
    lines.append('姓名: ' + '，'.join(p for p in name_parts if p))
    if patient.disease_type:
        lines.append(f'诊断: {patient.get_disease_type_display()}')
    if patient.admission_time:
        lines.append(f'入院时间: {timezone.localtime(patient.admission_time).strftime("%Y-%m-%d %H:%M")}')
    plans = list(patient.surgery_plans.all())
    if plans:
        plan_text = '；'.join(
            timezone.localtime(p.planned_time).strftime('%m-%d %H:%M') + (f'（{p.note}）' if p.note else '')
            for p in plans
        )
        lines.append(f'计划手术时间: {plan_text}')
    if patient.stay_range_text != '—':
        lines.append(f'预计在院: {patient.stay_range_text}')
    if patient.cautions:
        lines.append(f'注意事项: {patient.cautions}')
    lines.append('（提示：仅当患者问及本人相关事项时参考以上信息，避免编造）')
    return '\n'.join(lines)
