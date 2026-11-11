"""
Multi-provider AI client for the analytics page.

Supports Ollama (native /api/chat + /api/tags), OpenAI, DeepSeek, and any
OpenAI-compatible endpoint (custom_openai) via the installed `openai` SDK.

Scope: this module ONLY serves the data-analysis features (keyword/topic
extraction of QARecords and AI insight summaries).  The chat pipeline keeps
using its existing Ollama-only path — do not wire this module into chat.
"""
import json
import re

import requests

from .models import AIConfig

PROVIDER_DEFAULT_BASE_URLS = {
    'ollama': 'http://127.0.0.1:11434',
    'openai': 'https://api.openai.com/v1',
    'deepseek': 'https://api.deepseek.com/v1',
    'custom_openai': '',  # user must fill in
}


class AIClientError(Exception):
    """Unified error for AI service calls (message is Chinese, user-facing)."""


def get_active_config():
    """Return the active AIConfig row, or None if not configured."""
    return AIConfig.objects.filter(is_active=True).order_by('-updated_at').first()


def normalize_base_url(provider_type, base_url):
    """Fill the default address when the user leaves it blank."""
    base_url = (base_url or '').strip().rstrip('/')
    if base_url:
        return base_url
    return PROVIDER_DEFAULT_BASE_URLS.get(provider_type, '')


def fetch_models(provider_type, base_url='', api_key=''):
    """Fetch available model names.  Returns (ok, models) or (False, error)."""
    base_url = normalize_base_url(provider_type, base_url)
    if not base_url:
        return False, '请填写 API 地址'
    try:
        if provider_type == 'ollama':
            # Native Ollama tags endpoint — same style as views._fetch_ollama_models
            response = requests.get(f'{base_url}/api/tags', timeout=8)
            response.raise_for_status()
            payload = response.json()
            models = [item.get('name', '') for item in payload.get('models', []) if item.get('name')]
            return True, models
        # openai / deepseek / custom_openai all use the OpenAI-compatible models.list()
        client = _build_client(base_url, api_key)
        listing = client.models.list()
        models = [m.id for m in listing.data if getattr(m, 'id', '')]
        return True, models
    except requests.RequestException as e:
        return False, f'无法连接 AI 服务：{type(e).__name__}: {e}'
    except Exception as e:
        # Some compatible services disable the models endpoint — let the user type the name manually
        return False, f'无法获取模型列表（{type(e).__name__}: {e}），请手动填写模型名'


def _build_client(base_url, api_key):
    """Build an OpenAI SDK client (works for OpenAI/DeepSeek/custom compatible endpoints)."""
    import httpx
    from openai import OpenAI

    return OpenAI(
        base_url=base_url,
        api_key=api_key or 'not-needed',
        timeout=httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0),
        max_retries=1,
    )


def chat_complete_text(*, cfg=None, system, user_text, temperature=0.3, timeout=180):
    """
    Plain-text completion (used by the AI insight summary).

    Raises AIClientError on any failure.
    """
    cfg = cfg or get_active_config()
    if cfg is None:
        raise AIClientError('未配置 AI 服务，请先在页面顶部完成 AI 驱动配置')
    try:
        if cfg.provider_type == 'ollama':
            base_url = normalize_base_url('ollama', cfg.base_url)
            response = requests.post(
                f'{base_url}/api/chat',
                json={
                    'model': cfg.model,
                    'messages': [
                        {'role': 'system', 'content': system},
                        {'role': 'user', 'content': user_text},
                    ],
                    'stream': False,
                },
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            content = (payload.get('message') or {}).get('content', '')
            if not content:
                raise AIClientError(f'AI 服务返回为空：{payload.get("error", "unknown")}')
            return content
        client = _build_client(normalize_base_url(cfg.provider_type, cfg.base_url), cfg.api_key)
        completion = client.chat.completions.create(
            model=cfg.model,
            messages=[
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': user_text},
            ],
            temperature=temperature,
        )
        content = (completion.choices[0].message.content or '') if completion.choices else ''
        if not content:
            raise AIClientError('AI 服务返回为空')
        return content
    except AIClientError:
        raise
    except requests.RequestException as e:
        raise AIClientError(f'连接 AI 服务失败：{type(e).__name__}: {e}') from e
    except Exception as e:
        raise AIClientError(f'AI 调用失败：{type(e).__name__}: {e}') from e


def chat_complete_json(*, cfg=None, system, user_text, timeout=180):
    """
    Completion expected to be pure JSON (keyword/topic extraction).

    Returns a dict/list, or None when the reply cannot be parsed as JSON.
    Raises AIClientError on connection-level failures (after 1 retry).
    """
    last_error = None
    for attempt in (1, 2):
        try:
            text = chat_complete_text(cfg=cfg, system=system, user_text=user_text,
                                      temperature=0, timeout=timeout)
            return parse_model_json(text)
        except AIClientError as e:
            last_error = e
            if attempt == 1:
                continue  # one retry for flaky connections
    raise last_error or AIClientError('AI 调用失败')


def parse_model_json(content):
    """
    Robustly parse JSON out of a model reply.

    Handles: fenced ```json blocks (also appearing mid-text), bare JSON,
    leading prose before the JSON, trailing text, and top-level arrays.
    Raises ValueError when nothing parseable is found.
    """
    if not isinstance(content, str) or not content.strip():
        raise ValueError('empty content')

    text = content.strip()

    # 1) strip fenced code blocks wherever they appear
    fenced = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if fenced:
        text = fenced.group(1).strip()
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass  # fall through to the generic strategies

    # 2) direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # 3) greedy extract the first balanced {...} — same spirit as the existing
    #    ai_extract_knowledge helper, extended to objects appearing mid-text
    obj = _extract_balanced(text, '{', '}')
    if obj is not None:
        try:
            return json.loads(obj)
        except (json.JSONDecodeError, ValueError):
            pass

    # 4) greedy extract the first balanced [...]
    arr = _extract_balanced(text, '[', ']')
    if arr is not None:
        try:
            return json.loads(arr)
        except (json.JSONDecodeError, ValueError):
            pass

    raise ValueError(f'无法从 AI 回复中解析 JSON：{text[:200]}')


def _extract_balanced(text, open_char, close_char):
    """Return the first balanced open..close substring, or None."""
    start = text.find(open_char)
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None
