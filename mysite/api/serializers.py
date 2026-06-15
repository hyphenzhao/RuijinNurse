"""DRF Serializers for RuijinNurse mobile API."""
import re
from datetime import datetime

from rest_framework import serializers

from promotions.models import Agent, KnowledgeDocument


class AgentSerializer(serializers.ModelSerializer):
    """Serialize an Agent (Ollama-backed AI agent)."""

    class Meta:
        model = Agent
        fields = [
            'id',
            'name',
            'slug',
            'ollama_host',
            'ollama_port',
            'ollama_model',
            'system_prompt',
            'knowledge',
            'is_active',
        ]
        read_only_fields = ['id']


class KnowledgeDocumentSerializer(serializers.ModelSerializer):
    """Serialize an uploaded knowledge document."""

    class Meta:
        model = KnowledgeDocument
        fields = ['id', 'title', 'content', 'created_at']
        read_only_fields = ['id', 'created_at']


class ChatRequestSerializer(serializers.Serializer):
    """Validate a chat request from the mobile client."""
    text = serializers.CharField(max_length=10000, required=True)
    model = serializers.CharField(
        max_length=200,
        required=True,
        help_text='Format: "agent:{slug}", "model:{name}", or legacy local key',
    )
    session_id = serializers.CharField(max_length=100, required=False)


class ChatMessageSerializer(serializers.Serializer):
    """A single chat message within a session."""
    role = serializers.CharField()
    content = serializers.CharField()
    timestamp = serializers.DateTimeField(required=False)


class ChatSessionSerializer(serializers.Serializer):
    """Chat session summary for the session list."""
    id = serializers.CharField()
    model_key = serializers.CharField()
    created_at = serializers.DateTimeField(required=False)
    updated_at = serializers.DateTimeField(required=False)
    message_count = serializers.IntegerField()


class ChatSessionDetailSerializer(serializers.Serializer):
    """Full chat session including messages."""
    id = serializers.CharField()
    version = serializers.IntegerField()
    model_key = serializers.CharField()
    system_context = serializers.CharField()
    messages = ChatMessageSerializer(many=True)
    updated_at = serializers.DateTimeField(required=False)


class ModelChoiceSerializer(serializers.Serializer):
    """A single model choice for the mobile picker."""
    key = serializers.CharField()
    label = serializers.CharField()


class HealthResponseSerializer(serializers.Serializer):
    """Health check response."""
    ok = serializers.BooleanField()
    server_time = serializers.DateTimeField()
    authenticated = serializers.BooleanField()
    version = serializers.CharField()


class LoginSerializer(serializers.Serializer):
    """Login request."""
    username = serializers.CharField()
    password = serializers.CharField(style={'input_type': 'password'})


class LoginResponseSerializer(serializers.Serializer):
    """Login response with tokens and user info."""
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = serializers.DictField()


class RefreshSerializer(serializers.Serializer):
    """Token refresh request."""
    refresh = serializers.CharField()


class RefreshResponseSerializer(serializers.Serializer):
    """Token refresh response."""
    access = serializers.CharField()


class SettingsSerializer(serializers.Serializer):
    """App settings for the mobile client."""
    ollama_host = serializers.CharField(default='127.0.0.1')
    ollama_port = serializers.IntegerField(default=11434)
    whisper_host = serializers.CharField(default='127.0.0.1')
    whisper_port = serializers.CharField(default='8001')
    piper_host = serializers.CharField(default='127.0.0.1')
    piper_port = serializers.CharField(default='8002')
