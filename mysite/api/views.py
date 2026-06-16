"""REST API views for the RuijinNurse iOS/mobile client.

All endpoints live under /api/v1/ and use JWT authentication (DRF simplejwt).
SSE streaming reuses the shared generator extracted to promotions.view_helper.
"""
import json
import re
from pathlib import Path
from datetime import datetime

from django.conf import settings as django_settings
from django.contrib.auth import authenticate
from django.http import StreamingHttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from promotions.models import Agent, KnowledgeDocument
from promotions.view_helper import (
    generate_sse_stream,
    load_role_and_knowledge,
    _resolve_session_identifier,
    _safe_model_key,
    helper_sse,
    route_and_retrieve,
)
from .serializers import (
    AgentSerializer,
    ChatRequestSerializer,
    ChatSessionSerializer,
    ChatSessionDetailSerializer,
    HealthResponseSerializer,
    LoginSerializer,
    LoginResponseSerializer,
    ModelChoiceSerializer,
    RefreshSerializer,
    RefreshResponseSerializer,
    SettingsSerializer,
)


# ---------------------------------------------------------------------------
#  Path helpers (replicate promotions/views.py constants)
# ---------------------------------------------------------------------------
PROMOTIONS_STATIC_DIR = Path(__file__).resolve().parents[1] / 'promotions' / 'static' / 'promotions'
RUNTIME_SESSIONS_DIR = Path(__file__).resolve().parents[1] / 'promotions' / 'runtime_sessions'
SINGLE_MODEL_CONFIG_PATH = PROMOTIONS_STATIC_DIR / 'single_model_config.json'

OLLAMA_TIMEOUT_SECONDS = 8


# ---------------------------------------------------------------------------
#  Internal helpers — replicate logic from promotions/views.py
# ---------------------------------------------------------------------------

def _get_base_path() -> str:
    return str(PROMOTIONS_STATIC_DIR)


def _load_single_model_config():
    if SINGLE_MODEL_CONFIG_PATH.exists():
        try:
            return json.loads(SINGLE_MODEL_CONFIG_PATH.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'ollama_host': '127.0.0.1', 'ollama_port': 11434}


def _build_ollama_base_url(host: str, port) -> str:
    return f'http://{str(host).strip()}:{str(port).strip()}'


def _fetch_ollama_models(host: str, port):
    import requests as _requests
    response = _requests.get(
        f"{_build_ollama_base_url(host, port)}/api/tags",
        timeout=OLLAMA_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    return [model.get('name') for model in payload.get('models', []) if model.get('name')]


def _get_chat_target(model_key: str):
    """Resolve a model key to an Ollama chat target.

    Supported formats:
        'agent:{slug}'  — use a configured Agent
        'model:{name}'  — use a raw Ollama model name with the default host
        otherwise        — treat as a legacy local model key
    """
    if model_key.startswith('agent:'):
        slug = model_key.split(':', 1)[1]
        agent = get_object_or_404(Agent, slug=slug, is_active=True)
        return {
            'base_url': _build_ollama_base_url(agent.ollama_host, agent.ollama_port),
            'model_name': agent.ollama_model,
            'role_text': agent.system_prompt or '',
            'knowledge_text': agent.knowledge or '',
        }

    if model_key.startswith('model:'):
        config = _load_single_model_config()
        return {
            'base_url': _build_ollama_base_url(
                config.get('ollama_host', '127.0.0.1'),
                config.get('ollama_port', 11434),
            ),
            'model_name': model_key.split(':', 1)[1],
            'role_text': None,
            'knowledge_text': None,
        }

    # Legacy local model key — kept for backward compat
    LOCAL_MODEL_MAP = {
        'l-deepseek': 'deepseek-r1:32b',
        'l-gemma': 'gemma3:27b',
        'l-other': 'gemma3:27b',
    }
    if model_key not in LOCAL_MODEL_MAP:
        raise ValueError(f'不支持的模型选项: {model_key}')
    return {
        'base_url': _build_ollama_base_url('127.0.0.1', '11434'),
        'model_name': LOCAL_MODEL_MAP[model_key],
        'role_text': None,
        'knowledge_text': None,
    }


def _get_session_context_path_for_user(user_id, model_key: str) -> Path:
    """Build session file path keyed by user ID (for mobile JWT auth)."""
    sid = _resolve_session_identifier(user_id)
    RUNTIME_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return RUNTIME_SESSIONS_DIR / f'{sid}__{_safe_model_key(model_key)}.json'


# ---------------------------------------------------------------------------
#  Auth views
# ---------------------------------------------------------------------------

class LoginView(APIView):
    """POST /api/v1/auth/login/ — obtain JWT tokens."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        username = serializer.validated_data['username']
        password = serializer.validated_data['password']
        user = authenticate(request, username=username, password=password)

        if user is None:
            return Response(
                {'detail': '用户名或密码错误'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        refresh = RefreshToken.for_user(user)
        return Response({
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': {
                'id': user.id,
                'username': user.username,
                'is_staff': user.is_staff,
            },
        })


class RefreshView(APIView):
    """POST /api/v1/auth/refresh/ — refresh JWT access token."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RefreshSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            refresh = RefreshToken(serializer.validated_data['refresh'])
            return Response({'access': str(refresh.access_token)})
        except Exception:
            return Response(
                {'detail': 'Token 已失效或无效'},
                status=status.HTTP_401_UNAUTHORIZED,
            )


class LogoutView(APIView):
    """POST /api/v1/auth/logout/ — blacklist refresh token."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get('refresh')
        if refresh_token:
            try:
                token = RefreshToken(refresh_token)
                token.blacklist()
            except Exception:
                pass
        return Response({'detail': '已登出'})


# ---------------------------------------------------------------------------
#  Chat views
# ---------------------------------------------------------------------------

class ChatStreamView(APIView):
    """POST /api/v1/chat/stream/ — SSE streaming chat.

    Reuses the shared generate_sse_stream() from promotions.view_helper.
    Authentication: JWT (via IsAuthenticated).
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        promotion = serializer.validated_data['text']
        model_key = serializer.validated_data['model']

        base_path = _get_base_path()
        default_role_text, default_knowledge_text = load_role_and_knowledge(base_path)

        chat_target = _get_chat_target(model_key)
        session_path = _get_session_context_path_for_user(request.user.id, model_key)

        # ── RAG: augment knowledge for KG agents ──
        if model_key.startswith('agent:'):
            try:
                slug = model_key.split(':', 1)[1]
                agent = Agent.objects.get(slug=slug, is_active=True)
                rag_result = route_and_retrieve(agent, promotion)
                if rag_result and rag_result.get('context'):
                    augmented = (chat_target.get('knowledge_text') or default_knowledge_text or '')
                    augmented += '\n\n' + rag_result['context']
                    chat_target['knowledge_text'] = augmented
            except Agent.DoesNotExist:
                pass

        def generate():
            yield helper_sse('status', {'message': 'thinking'})
            for sse_event in generate_sse_stream(
                chat_target=chat_target,
                default_role_text=default_role_text,
                default_knowledge_text=default_knowledge_text,
                promotion=promotion,
                model_key=model_key,
                session_path=session_path,
                chat_history_callback=None,  # mobile handles history locally
            ):
                yield sse_event

        response = StreamingHttpResponse(generate(), content_type='text/event-stream')
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'
        return response


class ChatStopView(APIView):
    """POST /api/v1/chat/stop/ — acknowledge a stop request.

    The actual stream abort happens client-side (by closing the SSE connection).
    This endpoint provides a server-side acknowledgment.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        return Response({'ok': True})


# ---------------------------------------------------------------------------
#  Agents
# ---------------------------------------------------------------------------

class AgentListView(APIView):
    """GET /api/v1/agents/ — list all active agents (default first)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        agents = Agent.objects.filter(is_active=True).order_by('-is_default', 'name')
        serializer = AgentSerializer(agents, many=True)
        return Response(serializer.data)


class AgentSetDefaultView(APIView):
    """POST /api/v1/agents/{slug}/default/ — set an agent as default."""

    permission_classes = [IsAuthenticated]

    def post(self, request, slug):
        agent = get_object_or_404(Agent, slug=slug, is_active=True)
        Agent.objects.filter(is_default=True).update(is_default=False)
        agent.is_default = True
        agent.save()
        return Response({'ok': True})


# ---------------------------------------------------------------------------
#  Models
# ---------------------------------------------------------------------------

class ModelListView(APIView):
    """GET /api/v1/models/ — list available Ollama models.

    Query params:
        host (str) — Ollama host, default 127.0.0.1
        port (int) — Ollama port, default 11434
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        host = request.query_params.get('host', '127.0.0.1').strip()
        port = request.query_params.get('port', '11434').strip()

        if not host or not port:
            return Response(
                {'ok': False, 'error': '请提供 host 和 port 参数'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            models = _fetch_ollama_models(host, port)
            return Response({
                'ok': True,
                'models': models,
                'choices': [
                    {'key': f'model:{name}', 'label': f'本地-{name}'}
                    for name in models
                ],
            })
        except Exception as e:
            return Response(
                {'ok': False, 'error': f'{type(e).__name__}: {e}', 'models': []},
                status=status.HTTP_502_BAD_GATEWAY,
            )


# ---------------------------------------------------------------------------
#  Sessions
# ---------------------------------------------------------------------------

class SessionListView(APIView):
    """GET /api/v1/sessions/ — list current user's chat sessions."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        sid_prefix = _resolve_session_identifier(request.user.id)
        RUNTIME_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

        sessions = []
        for path in sorted(
            RUNTIME_SESSIONS_DIR.glob(f'{sid_prefix}__*.json'),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(data, dict):
                    continue
                sessions.append({
                    'id': path.stem,
                    'model_key': data.get('model_key', 'unknown'),
                    'message_count': len(data.get('messages', [])),
                    'updated_at': data.get('updated_at'),
                })
            except Exception:
                continue

        return Response(sessions)


class SessionDetailView(APIView):
    """GET /api/v1/sessions/{id}/ — get full session detail.
    DELETE /api/v1/sessions/{id}/ — delete a session.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, session_id):
        path = RUNTIME_SESSIONS_DIR / f'{session_id}.json'
        if not path.exists():
            return Response({'detail': '会话不存在'}, status=status.HTTP_404_NOT_FOUND)

        # Security: only allow access to own sessions
        sid_prefix = _resolve_session_identifier(request.user.id)
        if not session_id.startswith(sid_prefix):
            return Response({'detail': '无权访问此会话'}, status=status.HTTP_403_FORBIDDEN)

        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return Response({'detail': '会话数据损坏'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({
            'id': session_id,
            'version': data.get('version', 1),
            'model_key': data.get('model_key', 'unknown'),
            'system_context': data.get('system_context', ''),
            'messages': data.get('messages', []),
            'updated_at': data.get('updated_at'),
        })

    def delete(self, request, session_id):
        path = RUNTIME_SESSIONS_DIR / f'{session_id}.json'

        # Security: only allow deletion of own sessions
        sid_prefix = _resolve_session_identifier(request.user.id)
        if not session_id.startswith(sid_prefix):
            return Response({'detail': '无权操作此会话'}, status=status.HTTP_403_FORBIDDEN)

        if path.exists():
            try:
                path.unlink()
            except Exception as e:
                return Response(
                    {'detail': f'删除失败: {e}'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        return Response({'detail': '已删除'})


# ---------------------------------------------------------------------------
#  Health
# ---------------------------------------------------------------------------

class HealthView(APIView):
    """GET /api/v1/health/ — connectivity check for the mobile app."""

    permission_classes = []  # Allow anyone — this is a connectivity check
    authentication_classes = []  # Skip auth to avoid 401

    def get(self, request):
        return Response({
            'ok': True,
            'server_time': datetime.now().isoformat(),
            'authenticated': request.user.is_authenticated,
            'version': '1.0.0',
        })


# ---------------------------------------------------------------------------
#  Settings
# ---------------------------------------------------------------------------

class SettingsView(APIView):
    """GET /api/v1/settings/ — get current Ollama/Whisper/Piper configuration.
    PUT /api/v1/settings/ — update configuration.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        config = _load_single_model_config()
        return Response({
            'ollama_host': config.get('ollama_host', '127.0.0.1'),
            'ollama_port': config.get('ollama_port', 11434),
            'whisper_host': getattr(django_settings, 'WHISPER_HOST', '127.0.0.1'),
            'whisper_port': getattr(django_settings, 'WHISPER_PORT', '8001'),
            'piper_host': getattr(django_settings, 'PIPER_HOST', '127.0.0.1'),
            'piper_port': getattr(django_settings, 'PIPER_PORT', '8002'),
        })
