from django.db import models


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
