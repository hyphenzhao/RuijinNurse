from django import forms

from .models import Agent, KnowledgeGraph


class AgentForm(forms.ModelForm):
    """旧版智能体表单 — 使用文本知识字段"""
    class Meta:
        model = Agent
        fields = [
            'name', 'slug',
            'ollama_host', 'ollama_port', 'ollama_model',
            'system_prompt', 'knowledge', 'is_active',
        ]
        labels = {
            'name': '智能体名称',
            'slug': '标识（slug）',
            'ollama_host': 'Ollama IP / 主机名',
            'ollama_port': 'Ollama 端口',
            'ollama_model': 'Ollama 模型名',
            'system_prompt': '角色上下文（system context）',
            'knowledge': '参考知识（并入系统上下文）',
            'is_active': '是否启用',
        }


class KnowledgeGraphAgentForm(forms.ModelForm):
    """知识图谱版智能体表单 — 选择知识图谱替代文本知识"""
    knowledge_graphs = forms.ModelMultipleChoiceField(
        queryset=KnowledgeGraph.objects.filter(is_active=True),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label='选择知识图谱',
        help_text='可多选，选中的知识图谱将在对话时用于检索'
    )

    class Meta:
        model = Agent
        fields = [
            'name', 'slug',
            'ollama_host', 'ollama_port', 'ollama_model',
            'system_prompt', 'knowledge_graphs', 'is_active',
        ]
        labels = {
            'name': '智能体名称',
            'slug': '标识（slug）',
            'ollama_host': 'Ollama IP / 主机名',
            'ollama_port': 'Ollama 端口',
            'ollama_model': 'Ollama 模型名',
            'system_prompt': '角色上下文（system context）',
            'knowledge_graphs': '选择知识图谱',
            'is_active': '是否启用',
        }


class PromotionForm(forms.Form):
    LOCAL_MODEL_CHOICES = [
        ('l-deepseek', '本地-深度求索'),
        ('l-gemma', '本地-Gemma'),
        ('l-other', '本地-其他'),
    ]

    promotion_text = forms.CharField(widget=forms.Textarea, label='Your Promotion')
    model_select = forms.ChoiceField(label='选择模型')

    def __init__(self, *args, **kwargs):
        agent_choices = kwargs.pop('agent_choices', [])
        model_choices = kwargs.pop('model_choices', self.LOCAL_MODEL_CHOICES)
        super().__init__(*args, **kwargs)

        dynamic_agent_choices = [
            (f'agent:{a.slug}', f'智能体（{a.name}）')
            for a in agent_choices
        ]
        self.fields['model_select'].choices = dynamic_agent_choices + model_choices
        self.fields['model_select'].widget.attrs.update({
            'id': 'modelSelect',
            'class': 'form-control',
        })
