from django.db import models

class Agent(models.Model):
    """
    An AI agent backed by an Ollama model.
    """
    name = models.CharField(max_length=100, unique=True, help_text="显示给用户看的智能体名称")
    slug = models.SlugField(max_length=100, unique=True, help_text="用作下拉框 value 的标识，例如 'nurse-agent'")
    ollama_model = models.CharField(
        max_length=100,
        help_text="对应 Ollama 模型名，如 deepseek-r1:32b、gemma3:27b"
    )
    system_prompt = models.TextField(blank=True, help_text="该智能体的角色设定 / system prompt")
    knowledge = models.TextField(blank=True, help_text="该智能体的背景知识或说明文本")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

class KnowledgeDocument(models.Model):
    title = models.CharField(max_length=255)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title
