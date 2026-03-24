from django import forms
from .models import *
from django.contrib.auth import get_user_model

class AgentForm(forms.ModelForm):
    class Meta:
        model = Agent
        fields = ['name', 'slug', 'ollama_model', 'system_prompt', 'knowledge', 'is_active']
        labels = {
            'name': '智能体名称',
            'slug': '标识（slug）',
            'ollama_model': 'Ollama 模型名',
            'system_prompt': '角色设定（system prompt）',
            'knowledge': '背景知识',
            'is_active': '是否启用',
        }


# Form class for promotion submission
class PromotionForm(forms.Form):
    promotion_text = forms.CharField(widget=forms.Textarea, label="Your Promotion")
    model_select = forms.ChoiceField(label="选择模型")

    def __init__(self, *args, **kwargs):
        agent_choices = kwargs.pop('agent_choices', [])
        super().__init__(*args, **kwargs)

        base_model_choices = [
            ('l-deepseek', '本地-深度求索'),
            ('l-gemma', '本地-Gemma'),
            ('r-kimi', '云端-Kimi'),
            ('r-baidu', '云端-文心一言'),
            ('l-ziya', '本地-姜子牙'),
            ('l-other', '本地-其他'),
        ]

        dynamic_agent_choices = [
            (f'agent:{a.slug}', f'智能体（{a.name}）')
            for a in agent_choices
        ]

        self.fields['model_select'].choices =  dynamic_agent_choices + base_model_choices

        self.fields['model_select'].widget.attrs.update({
            'id': 'modelSelect',
            'class': 'form-control',
        })
