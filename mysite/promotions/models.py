import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class Agent(models.Model):
    """
    An AI agent backed by an Ollama model.
    """
    AGENT_TYPE_CHOICES = [
        ('legacy', '旧版 — 文本知识'),
        ('kg', '知识图谱版 — 结构化知识检索'),
    ]

    name = models.CharField(max_length=100, unique=True, help_text="显示给用户看的智能体名称")
    slug = models.SlugField(max_length=100, unique=True, help_text="用作下拉框 value 的标识，例如 'nurse-agent'")
    ollama_host = models.CharField(
        max_length=100,
        default='127.0.0.1',
        help_text='Ollama 服务 IP 或主机名，例如 127.0.0.1'
    )
    ollama_port = models.PositiveIntegerField(
        default=11434,
        help_text='Ollama 服务端口，例如 11434'
    )
    ollama_model = models.CharField(
        max_length=100,
        help_text='对应 Ollama 模型名，如 deepseek-r1:32b、gemma3:27b'
    )
    system_prompt = models.TextField(blank=True, help_text='该智能体的角色设定 / system prompt')
    knowledge = models.TextField(blank=True, help_text='该智能体的背景知识或说明文本（旧版使用）')
    is_active = models.BooleanField(default=True)

    # ── 新增字段 ──
    agent_type = models.CharField(
        max_length=20, choices=AGENT_TYPE_CHOICES, default='legacy',
        help_text='智能体类型：legacy=文本知识，kg=知识图谱检索'
    )
    knowledge_graphs = models.ManyToManyField(
        'KnowledgeGraph', blank=True, related_name='agents',
        help_text='知识图谱版智能体绑定的知识图谱（可多选）'
    )
    is_default = models.BooleanField(
        default=False,
        help_text='是否默认智能体（网页端和 App 端优先选中）'
    )

    class Meta:
        ordering = ['-is_default', '-is_active', 'name']

    def __str__(self):
        return self.name


class KnowledgeDocument(models.Model):
    title = models.CharField(max_length=255)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


# ══════════════════════════════════════════════════════════════
#  知识图谱系统
# ══════════════════════════════════════════════════════════════

class KnowledgeGraph(models.Model):
    """知识图谱配置 — 绑定 Ollama embedding 模型 + AI 提取模型"""
    name = models.CharField(max_length=100, unique=True, help_text='知识图谱名称')
    ollama_host = models.CharField(max_length=100, default='127.0.0.1',
                                   help_text='Ollama 服务主机（embedding + 提取共用）')
    ollama_port = models.PositiveIntegerField(default=11434,
                                              help_text='Ollama 服务端口')
    embedding_model = models.CharField(max_length=100, blank=True,
                                       help_text='用于向量化的 Ollama 模型名')
    extraction_model = models.CharField(max_length=100, blank=True,
                                        help_text='用于 AI 自动提取知识的 Ollama 模型名（如 qwen3:4b）')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class ReferenceCard(models.Model):
    """流程指南卡 — 高精度关键词匹配的结构化指南"""
    CATEGORY_CHOICES = [
        ('admission', '住院相关'),
        ('discharge', '出院相关'),
        ('medication', '用药指导'),
        ('rehab', '康复训练'),
        ('diet', '饮食管理'),
        ('safety', '安全管理'),
        ('followup', '随访复查'),
        ('general', '综合'),
    ]
    MEDIA_CHOICES = [
        ('video', '视频'),
        ('image', '图片'),
        ('pdf', 'PDF'),
        ('none', '无'),
    ]

    knowledge_graph = models.ForeignKey(
        KnowledgeGraph, on_delete=models.CASCADE, related_name='reference_cards'
    )
    title = models.CharField(max_length=200, help_text='指南标题')
    trigger_keywords = models.TextField(
        blank=True,
        help_text='触发关键词，每行一个。匹配到任一关键词时命中该指南'
    )
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='general')
    media_type = models.CharField(max_length=10, choices=MEDIA_CHOICES, default='none')
    media_url = models.CharField(max_length=500, blank=True, help_text='视频/图片/PDF 链接')
    media_timespan = models.CharField(max_length=50, blank=True, help_text='时间区间，如 00:09-01:39')
    key_points = models.JSONField(default=list, help_text='要点列表 [{"label":"标题","text":"内容"},...]')
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort_order', 'title']

    def __str__(self):
        return self.title


class KnowledgeEntry(models.Model):
    """专业知识条目 — 语义检索的知识片段"""
    knowledge_graph = models.ForeignKey(
        KnowledgeGraph, on_delete=models.CASCADE, related_name='knowledge_entries'
    )
    title = models.CharField(max_length=300, help_text='知识条目标题')
    content = models.TextField(help_text='知识内容')
    source = models.CharField(max_length=300, blank=True, help_text='知识来源，如《神经外科护理常规》')
    tags = models.CharField(max_length=500, blank=True, help_text='标签，逗号分隔')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class LiteratureEntry(models.Model):
    """学术文献条目"""
    EVIDENCE_CHOICES = [
        ('A', 'A级 — 多中心RCT'),
        ('B', 'B级 — 单中心研究'),
        ('C', 'C级 — 专家共识'),
        ('D', 'D级 — 病例报告'),
    ]

    knowledge_graph = models.ForeignKey(
        KnowledgeGraph, on_delete=models.CASCADE, related_name='literature'
    )
    title = models.CharField(max_length=500, help_text='文献标题')
    authors = models.CharField(max_length=300, blank=True, help_text='作者')
    year = models.IntegerField(null=True, blank=True, help_text='发表年份')
    journal = models.CharField(max_length=300, blank=True, help_text='期刊名称')
    doi = models.CharField(max_length=200, blank=True, help_text='DOI')
    abstract = models.TextField(help_text='摘要（用于检索匹配）')
    full_text = models.TextField(blank=True, help_text='全文')
    evidence_level = models.CharField(max_length=1, choices=EVIDENCE_CHOICES, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-year', '-created_at']

    def __str__(self):
        return self.title


# ══════════════════════════════════════════════════════════════
#  二期升级：患者管理 / 问答记录 / 数据分析 AI 配置
# ══════════════════════════════════════════════════════════════

DISEASE_TYPE_CHOICES = [
    ('parkinson', '帕金森病'),
    ('epilepsy', '癫痫'),
    ('dystonia', '肌张力障碍'),
    ('affective', '情感障碍'),
    ('schizophrenia', '精神分裂症'),
    ('other', '其他'),
]


class Patient(models.Model):
    """住院患者档案（功能神经外科病区）"""
    GENDER_CHOICES = [
        ('male', '男'),
        ('female', '女'),
    ]

    bed_no = models.CharField('床号', max_length=20, db_index=True, help_text='床位号，如 12')
    name = models.CharField('姓名', max_length=50)
    gender = models.CharField('性别', max_length=10, choices=GENDER_CHOICES, blank=True, default='')
    birth_date = models.DateField('出生日期', null=True, blank=True)
    disease_type = models.CharField('疾病种类', max_length=20, choices=DISEASE_TYPE_CHOICES, default='other')
    admission_time = models.DateTimeField('入院时间', null=True, blank=True)
    expected_stay_min_days = models.IntegerField('预计在院最少天数', null=True, blank=True)
    expected_stay_max_days = models.IntegerField('预计在院最多天数', null=True, blank=True)
    cautions = models.TextField('注意事项', blank=True, default='')
    notes = models.TextField('备注', blank=True, default='')
    scales = models.JSONField(
        '量表数据（预留）', default=dict, blank=True,
        help_text='预留字段：后续版本支持 UPDRS/MMSE/NPI 等量表录入，当前为空占位'
    )
    is_active = models.BooleanField('在院', default=True, help_text='出院/归档后置为 False，历史问答关联保留')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_active', 'bed_no', 'name']

    def __str__(self):
        return f'{self.bed_no}床 {self.name}'

    @property
    def age(self):
        """由出生日期计算周岁，未知时返回 None"""
        if not self.birth_date:
            return None
        today = timezone.localdate()
        years = today.year - self.birth_date.year
        if (today.month, today.day) < (self.birth_date.month, self.birth_date.day):
            years -= 1
        return years if years >= 0 else None

    @property
    def stay_range_text(self):
        """预计在院时间范围文案，如 '10 ~ 14 天'；缺失时返回 '—'"""
        if self.expected_stay_min_days and self.expected_stay_max_days:
            return f'{self.expected_stay_min_days} ~ {self.expected_stay_max_days} 天'
        if self.expected_stay_min_days:
            return f'≥ {self.expected_stay_min_days} 天'
        if self.expected_stay_max_days:
            return f'≤ {self.expected_stay_max_days} 天'
        return '—'


class SurgeryPlan(models.Model):
    """计划手术时间（一个患者可有多条，如分期手术）"""
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='surgery_plans')
    planned_time = models.DateTimeField('计划手术时间')
    note = models.CharField('备注（术式/说明）', max_length=200, blank=True, default='')

    class Meta:
        ordering = ['planned_time']

    def __str__(self):
        return f'{self.patient} @ {self.planned_time}'


class QARecord(models.Model):
    """问答记录 — 高频问题分析与数据可视化的数据来源"""
    SOURCE_CHOICES = [
        ('web', '网页端'),
        ('api', 'App端'),
    ]
    EXTRACTION_CHOICES = [
        ('pending', '待提取'),
        ('processing', '提取中'),
        ('done', '已完成'),
        ('error', '失败'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='qa_records', verbose_name='提问用户'
    )
    patient = models.ForeignKey(
        Patient, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='qa_records', verbose_name='关联患者'
    )
    agent = models.ForeignKey(
        Agent, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='qa_records', verbose_name='智能体'
    )
    source = models.CharField('来源', max_length=10, choices=SOURCE_CHOICES, default='web')
    session_scope = models.CharField('会话标识', max_length=120, blank=True, default='', db_index=True,
                                     help_text='web 为 Django session key，App 为 user_{id}')
    model_key = models.CharField('模型键', max_length=150, blank=True, default='',
                                 help_text='agent:{slug} / model:{name} / l-*')
    question = models.TextField('用户问题')
    answer = models.TextField('AI 回答', blank=True, default='')
    thinking = models.TextField('思考过程', blank=True, default='')
    error = models.TextField('错误信息', blank=True, default='',
                             help_text='服务端错误时记录；数据分析默认排除带错误记录')
    keywords = models.JSONField('关键词', default=list, blank=True)
    topics = models.JSONField('主题分类', default=list, blank=True)
    extraction_status = models.CharField('提取状态', max_length=20, choices=EXTRACTION_CHOICES, default='pending')
    extraction_error = models.TextField('提取错误详情', blank=True, default='')
    latency_ms = models.PositiveIntegerField('耗时(ms)', null=True, blank=True)
    created_at = models.DateTimeField('提问时间', auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at', 'extraction_status']),
            models.Index(fields=['patient', 'created_at']),
        ]

    def __str__(self):
        return f'#{self.pk} {self.question[:20]}'


class QATiming(models.Model):
    """One accepted web question, with separately measured server and browser times."""

    STATUS_CHOICES = [
        ('pending', '处理中'), ('success', '成功'), ('failed', '失败'),
        ('timeout', '超时'), ('cancelled', '已取消'), ('interrupted', '已中断'),
    ]

    request_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    qa_record = models.ForeignKey(QARecord, null=True, blank=True, on_delete=models.SET_NULL)
    session_tag = models.CharField(max_length=64, blank=True, default='')
    source = models.CharField(max_length=10, default='web')
    model_key = models.CharField(max_length=150, blank=True, default='')
    model_name = models.CharField(max_length=150, blank=True, default='')
    started_at = models.DateTimeField(db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='pending')
    backend_ms = models.PositiveIntegerField(null=True, blank=True)
    first_answer_ms = models.PositiveIntegerField(null=True, blank=True)
    client_first_ms = models.PositiveIntegerField(null=True, blank=True)
    client_total_ms = models.PositiveIntegerField(null=True, blank=True)


class AIConfig(models.Model):
    """数据分析页 AI 服务配置（关键词/主题提取与洞察简报用；多行并存，仅一行生效）"""
    PROVIDER_CHOICES = [
        ('ollama', 'Ollama'),
        ('openai', 'OpenAI'),
        ('deepseek', 'DeepSeek'),
        ('custom_openai', 'OpenAI 兼容接口'),
    ]

    provider_type = models.CharField('服务类型', max_length=20, choices=PROVIDER_CHOICES, default='ollama')
    base_url = models.CharField('API 地址', max_length=300, blank=True, default='',
                                help_text='Ollama 填根地址如 http://127.0.0.1:11434；其余填含 /v1 的完整地址')
    api_key = models.CharField('API Key', max_length=300, blank=True, default='')
    model = models.CharField('模型', max_length=150, blank=True, default='')
    is_active = models.BooleanField('启用', default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.provider_type} / {self.model or "-"}'
