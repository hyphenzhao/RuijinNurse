from pathlib import Path
import json
import re
import time
from datetime import datetime, timedelta

import requests
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie

from .ai_client import AIClientError, chat_complete_text, fetch_models, get_active_config
from .forms import AgentForm, KnowledgeGraphAgentForm, PromotionForm
from .models import (
    Agent, KnowledgeDocument, KnowledgeGraph,
    ReferenceCard, KnowledgeEntry, LiteratureEntry,
    Patient, SurgeryPlan, QARecord, AIConfig,
    DISEASE_TYPE_CHOICES,
)
from .qa_store import (
    TOPIC_WHITELIST,
    extract_record,
    format_patient_context,
    log_qa_record,
)
from .qa_timing import QATimer, TimedStream, cancel_timing, failure_status, ollama_request_timeout
from .view_helper import (
    ai_extract_knowledge,
    ai_extract_knowledge_batch,
    extract_files_from_upload,
    extract_text_from_upload,
    helper_sse,
    load_role_and_knowledge,
    route_and_retrieve,
)


PROMOTIONS_STATIC_DIR = Path(__file__).resolve().parent / 'static' / 'promotions'
RUNTIME_SESSIONS_DIR = Path(__file__).resolve().parent / 'runtime_sessions'
SINGLE_MODEL_CONFIG_PATH = PROMOTIONS_STATIC_DIR / 'single_model_config.json'
README_PATH = Path(__file__).resolve().parents[2] / 'README.md'
OLLAMA_CHAT_URL = 'http://localhost:11434/api/chat'
LOCAL_MODEL_MAP = {
    'l-deepseek': 'deepseek-r1:32b',
    'l-gemma': 'gemma3:27b',
    'l-other': 'gemma3:27b',
}
OLLAMA_TIMEOUT_SECONDS = 8


def _get_base_path() -> str:
    return str(PROMOTIONS_STATIC_DIR)


def _build_system_context(role_text: str, knowledge_text: str) -> str:
    role_text = (role_text or '').strip()
    knowledge_text = (knowledge_text or '').strip()
    parts = []
    if role_text:
        parts.append(f'[角色设定]\n{role_text}')
    if knowledge_text:
        parts.append(f'[参考知识]\n{knowledge_text}')
    return '\n\n'.join(parts).strip()


def _build_messages(system_context: str, history_messages, promotion: str):
    messages = []
    if system_context:
        messages.append({'role': 'system', 'content': system_context})
    if history_messages:
        messages.extend(history_messages)
    messages.append({'role': 'user', 'content': promotion})
    return messages


def _resolve_local_model(model_key: str) -> str:
    if model_key not in LOCAL_MODEL_MAP:
        raise ValueError(f'不支持的本地模型选项: {model_key}')
    return LOCAL_MODEL_MAP[model_key]


def _build_ollama_base_url(host: str, port) -> str:
    return f'http://{str(host).strip()}:{str(port).strip()}'


def _fetch_ollama_models(host: str, port):
    response = requests.get(
        f"{_build_ollama_base_url(host, port)}/api/tags",
        timeout=OLLAMA_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    return [model.get('name') for model in payload.get('models', []) if model.get('name')]


def _stream_ollama_response_by_target(base_url: str, model_name: str, messages):
    payload = {
        'model': model_name,
        'messages': messages,
        'think': True,
        'stream': True,
    }
    return requests.post(f'{base_url}/api/chat', json=payload, stream=True, timeout=ollama_request_timeout())


def _stream_ollama_response(model_key: str, role_text: str, knowledge_text: str, promotion: str):
    system_context = _build_system_context(role_text, knowledge_text)
    return _stream_ollama_response_by_target(
        _build_ollama_base_url('127.0.0.1', '11434'),
        _resolve_local_model(model_key),
        _build_messages(system_context, [], promotion),
    )


def _collect_ollama_response(model_key: str, role_text: str, knowledge_text: str, promotion: str) -> str:
    response_text = ''
    with _stream_ollama_response(model_key, role_text, knowledge_text, promotion) as response:
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            response_text += data.get('message', {}).get('content', '')
            if data.get('done', True):
                break

    if '</think>' in response_text:
        response_text = response_text.split('</think>')[-1]

    return response_text


def _get_runtime_model_choices():
    config = _load_single_model_config()
    host = config.get('ollama_host', '127.0.0.1')
    port = config.get('ollama_port', 11434)

    try:
        models = _fetch_ollama_models(host, port)
        if models:
            return [(f'model:{model_name}', f'本地-{model_name}') for model_name in models]
    except Exception:
        pass

    return PromotionForm.LOCAL_MODEL_CHOICES



def _get_chat_target(model_key: str):
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
            'base_url': _build_ollama_base_url(config.get('ollama_host', '127.0.0.1'), config.get('ollama_port', 11434)),
            'model_name': model_key.split(':', 1)[1],
            'role_text': None,
            'knowledge_text': None,
        }

    return {
        'base_url': _build_ollama_base_url('127.0.0.1', '11434'),
        'model_name': _resolve_local_model(model_key),
        'role_text': None,
        'knowledge_text': None,
    }


def _append_chat_history(request, promotion: str, response_text: str, model_key='default'):
    scope = f'{request.session.get("current_patient_id") or "none"}:{model_key}'
    histories = request.session.get('chat_history_scopes', {})
    chat_history = histories.get(scope, [])
    chat_history.append({'user': promotion, 'response': response_text})
    histories[scope] = chat_history
    request.session['chat_history_scopes'] = histories
    return chat_history


def _split_thinking_delta(delta: str, state: dict):
    """
    Split one streamed Ollama delta into visible thinking content and final answer content.
    state = {'in_think': bool}
    Returns: (thinking_text, answer_text)
    """
    if not delta:
        return '', ''

    thinking_parts = []
    answer_parts = []
    buf = delta

    while buf:
        if state['in_think']:
            end = buf.find('</think>')
            if end == -1:
                thinking_parts.append(buf)
                buf = ''
            else:
                thinking_parts.append(buf[:end])
                buf = buf[end + len('</think>'):]
                state['in_think'] = False
            continue

        start = buf.find('<think>')
        if start == -1:
            answer_parts.append(buf)
            buf = ''
        else:
            if start > 0:
                answer_parts.append(buf[:start])
            buf = buf[start + len('<think>'):]
            state['in_think'] = True

    return ''.join(thinking_parts), ''.join(answer_parts)


def _save_latest_response_text(response_text: str):
    PROMOTIONS_STATIC_DIR.mkdir(parents=True, exist_ok=True)
    input_fpath = PROMOTIONS_STATIC_DIR / 'input.txt'
    input_fpath.write_text(response_text.replace('**', '').replace('\n', ''), encoding='utf-8')


def _load_single_model_config():
    if SINGLE_MODEL_CONFIG_PATH.exists():
        try:
            return json.loads(SINGLE_MODEL_CONFIG_PATH.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {
        'ollama_host': '127.0.0.1',
        'ollama_port': 11434,
    }


def _save_single_model_config(host: str, port):
    PROMOTIONS_STATIC_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        'ollama_host': host,
        'ollama_port': int(port),
    }
    SINGLE_MODEL_CONFIG_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def _ensure_request_session_key(request) -> str:
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key


def _get_session_patient(request):
    """患者抽屉中当前关联的患者（存在且未归档时返回，否则 None）"""
    patient_id = request.session.get('current_patient_id')
    if not patient_id:
        return None
    patient = Patient.objects.filter(id=patient_id, is_active=True).first()
    if patient is None:
        request.session.pop('current_patient_id', None)
    return patient


def _append_patient_context(request, knowledge_text: str) -> str:
    """把当前关联患者的信息块拼入参考知识，供聊天 AI 参考"""
    patient = _get_session_patient(request)
    if patient is None:
        return knowledge_text
    patient_block = format_patient_context(patient)
    if not patient_block:
        return knowledge_text
    return f'{(knowledge_text or "").rstrip()}\n\n{patient_block}'


def _safe_model_key(model_key: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]+', '_', model_key or 'default')


def _get_session_context_path(request, model_key: str) -> Path:
    session_key = _ensure_request_session_key(request)
    RUNTIME_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    # Legacy files may contain multiple patients. Preserve them for audit, but do
    # not silently import their mixed context into a new patient's conversation.
    patient_id = request.session.get('current_patient_id') or 'none'
    return RUNTIME_SESSIONS_DIR / f'{session_key}__{_safe_model_key(model_key)}__patient_{patient_id}.json'


def _selected_patient_history(request, model_key):
    if not isinstance(model_key, str) or not model_key or len(model_key) > 150:
        return []
    path = _get_session_context_path(request, model_key)
    data = _load_session_context(path, '', model_key)
    history = []
    question = None
    for message in data.get('messages', []):
        if message.get('role') == 'user':
            question = message.get('content')
        elif message.get('role') == 'assistant' and question:
            history.append({'user': question, 'response': message.get('content', '')})
            question = None
    return history


def _load_session_context(path: Path, system_context: str, model_key: str):
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                data.setdefault('version', 1)
                data.setdefault('model_key', model_key)
                data.setdefault('system_context', system_context)
                data.setdefault('messages', [])
                return data
        except Exception:
            pass
    return {
        'version': 1,
        'model_key': model_key,
        'system_context': system_context,
        'messages': [],
        'updated_at': None,
    }


def _save_session_context(path: Path, data: dict):
    data['updated_at'] = datetime.now().isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def _append_session_message(data: dict, role: str, content: str):
    content = (content or '').strip()
    if not content:
        return
    data.setdefault('messages', [])
    data['messages'].append({
        'role': role,
        'content': content,
        'timestamp': datetime.now().isoformat(),
    })


def _history_messages_for_ollama(data: dict):
    out = []
    for item in data.get('messages', []):
        role = item.get('role')
        content = item.get('content')
        if role in {'user', 'assistant'} and content:
            out.append({'role': role, 'content': content})
    return out


def _clear_request_session_contexts(request):
    session_key = _ensure_request_session_key(request)
    if not RUNTIME_SESSIONS_DIR.exists():
        return
    for path in RUNTIME_SESSIONS_DIR.glob(f'{session_key}__*.json'):
        try:
            path.unlink()
        except Exception:
            pass


def ollama_models_view(request):
    host = request.GET.get('host', '127.0.0.1').strip()
    port = request.GET.get('port', '11434').strip()

    if not host or not port:
        return JsonResponse({'ok': False, 'error': '请先填写 Ollama IP 和端口。'}, status=400)

    try:
        models = _fetch_ollama_models(host, port)
    except Exception as e:
        return JsonResponse({'ok': False, 'error': f'{type(e).__name__}: {e}', 'models': []}, status=502)

    return JsonResponse({'ok': True, 'models': models})


def readme_help_view(request):
    try:
        content = README_PATH.read_text(encoding='utf-8')
    except Exception as e:
        return JsonResponse({'ok': False, 'error': f'{type(e).__name__}: {e}', 'content': ''}, status=500)
    return JsonResponse({'ok': True, 'content': content})


def promotion_stop_view(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST only'}, status=405)
    cancel_timing(request)
    return JsonResponse({'ok': True})


def ai_extract_view(request):
    """POST /promotion/setup/ai-extract/ — AI 提取结构化知识"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST only'}, status=405)

    kg_id = request.POST.get('kg_id', '')
    knowledge_type = request.POST.get('knowledge_type', '')
    raw_text = request.POST.get('text', '').strip()

    kg = get_object_or_404(KnowledgeGraph, id=kg_id)

    # Handle file upload
    if not raw_text and request.FILES.get('file'):
        try:
            raw_text = extract_text_from_upload(request.FILES['file'])
        except Exception as e:
            return JsonResponse({'ok': False, 'error': f'文件解析失败: {e}'})

    if not raw_text:
        return JsonResponse({'ok': False, 'error': '请提供文本或上传文件。'})

    success, result = ai_extract_knowledge(kg, knowledge_type, raw_text)
    if success:
        return JsonResponse({'ok': True, 'data': result})
    else:
        return JsonResponse({'ok': False, 'error': result})


def ai_batch_import_view(request):
    """POST /promotion/setup/ai-batch-import/ — AI 批量提取并创建多个流程指南卡"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST only'}, status=405)

    kg_id = request.POST.get('kg_id', '')
    raw_text = request.POST.get('text', '').strip()

    kg = get_object_or_404(KnowledgeGraph, id=kg_id)

    if not raw_text and request.FILES.get('file'):
        try:
            raw_text = extract_text_from_upload(request.FILES['file'])
        except Exception as e:
            return JsonResponse({'ok': False, 'error': f'文件解析失败: {e}'})

    if not raw_text:
        return JsonResponse({'ok': False, 'error': '请提供文本或上传文件。'})

    success, result = ai_extract_knowledge_batch(kg, raw_text)
    if not success:
        return JsonResponse({'ok': False, 'error': result})

    # Create cards from the extracted array
    created = []
    for i, card_data in enumerate(result):
        if not isinstance(card_data, dict) or not card_data.get('title'):
            continue
        try:
            kp = card_data.get('key_points', [])
            if isinstance(kp, str):
                try: kp = json.loads(kp)
                except: kp = []
            rc = ReferenceCard.objects.create(
                knowledge_graph=kg,
                title=card_data.get('title', f'导入指南 {i+1}'),
                trigger_keywords=card_data.get('trigger_keywords', ''),
                category=card_data.get('category', 'general'),
                media_type=card_data.get('media_type', 'none'),
                media_url=card_data.get('media_url', ''),
                media_timespan=card_data.get('media_timespan', ''),
                key_points=kp if isinstance(kp, list) else [],
                sort_order=i,
            )
            created.append({'id': rc.id, 'title': rc.title})
        except Exception as e:
            created.append({'title': card_data.get('title', '?'), 'error': str(e)})

    return JsonResponse({'ok': True, 'created': created, 'count': len(created)})


def ai_batch_stream_view(request):
    """POST /promotion/setup/ai-batch-stream/ — SSE 批量导入（多文件/zip，带进度）"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST only'}, status=405)

    kg_id = request.POST.get('kg_id', '')
    knowledge_type = request.POST.get('knowledge_type', 'knowledge_entry')

    kg = get_object_or_404(KnowledgeGraph, id=kg_id)
    if not kg.extraction_model:
        return JsonResponse(
            {'ok': False, 'error': '该知识图谱未配置 AI 提取模型。'}, status=400
        )

    files = request.FILES.getlist('files')
    if not files:
        return JsonResponse({'ok': False, 'error': '请上传文件。'}, status=400)

    # Extract text from all uploaded files / zip archives
    file_results = extract_files_from_upload(files)

    def generate():
        created_count = 0
        error_count = 0
        cancelled = False

        for i, fr in enumerate(file_results):
            if cancelled:
                break

            fname = fr['filename']
            text = fr['text']
            err = fr.get('error')

            if err or not text:
                yield helper_sse('progress', {
                    'index': i + 1, 'total': len(file_results),
                    'filename': fname, 'status': 'error',
                    'message': err or '无法提取文本',
                })
                error_count += 1
                continue

            yield helper_sse('progress', {
                'index': i + 1, 'total': len(file_results),
                'filename': fname, 'status': 'processing',
                'message': 'AI 正在分析...',
            })

            try:
                success, result = ai_extract_knowledge(kg, knowledge_type, text)
                if not success:
                    yield helper_sse('progress', {
                        'index': i + 1, 'total': len(file_results),
                        'filename': fname, 'status': 'error',
                        'message': result,
                    })
                    error_count += 1
                    continue

                # Create the entry
                if knowledge_type == 'knowledge_entry':
                    KnowledgeEntry.objects.create(
                        knowledge_graph=kg,
                        title=result.get('title', fname),
                        content=result.get('content', text[:500]),
                        source=result.get('source', ''),
                        tags=result.get('tags', ''),
                    )
                elif knowledge_type == 'literature':
                    LiteratureEntry.objects.create(
                        knowledge_graph=kg,
                        title=result.get('title', fname),
                        authors=result.get('authors', ''),
                        year=result.get('year') if isinstance(result.get('year'), int) else None,
                        journal=result.get('journal', ''),
                        doi=result.get('doi', ''),
                        abstract=result.get('abstract', text[:1000]),
                        full_text=result.get('full_text', ''),
                        evidence_level=result.get('evidence_level', ''),
                    )
                else:
                    yield helper_sse('progress', {
                        'index': i + 1, 'total': len(file_results),
                        'filename': fname, 'status': 'error',
                        'message': f'不支持的知识类型: {knowledge_type}',
                    })
                    error_count += 1
                    continue

                created_count += 1
                yield helper_sse('progress', {
                    'index': i + 1, 'total': len(file_results),
                    'filename': fname, 'status': 'done',
                    'message': result.get('title', '已导入'),
                    'entry_title': result.get('title', ''),
                })

            except GeneratorExit:
                cancelled = True
                break
            except Exception as e:
                yield helper_sse('progress', {
                    'index': i + 1, 'total': len(file_results),
                    'filename': fname, 'status': 'error',
                    'message': f'{type(e).__name__}: {e}',
                })
                error_count += 1

        yield helper_sse('done', {
            'created': created_count, 'errors': error_count, 'total': len(file_results),
            'cancelled': cancelled,
        })

    response = StreamingHttpResponse(generate(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


def setup_view(request):
    base_path = _get_base_path()
    role_file_path = Path(base_path) / 'role.txt'
    knowledge_file_path = Path(base_path) / 'knowledge.txt'
    role_text, knowledge_text = load_role_and_knowledge(base_path)

    agents = Agent.objects.all()
    agent_form = AgentForm()
    agent_kg_form = KnowledgeGraphAgentForm()
    single_model_config = _load_single_model_config()
    upload_message = None

    # ── Knowledge Graph data ──
    knowledge_graphs = KnowledgeGraph.objects.all()
    # Preload related data for display
    ref_cards_by_kg = {}
    kg_entries_by_kg = {}
    lit_entries_by_kg = {}
    for kg in knowledge_graphs:
        ref_cards_by_kg[kg.id] = kg.reference_cards.filter(is_active=True)
        kg_entries_by_kg[kg.id] = kg.knowledge_entries.filter(is_active=True)
        lit_entries_by_kg[kg.id] = kg.literature.filter(is_active=True)

    if request.method == 'POST':
        form_type = request.POST.get('form_type', '')
        kg_id = request.POST.get('kg_id', '')

        # ──────────────────────────────────────────
        #  Knowledge Graph CRUD
        # ──────────────────────────────────────────
        if form_type == 'kg_create':
            name = request.POST.get('kg_name', '').strip()
            host = request.POST.get('kg_host', '').strip()
            port = request.POST.get('kg_port', '').strip()
            emb_model = request.POST.get('kg_embedding_model', '').strip()
            ext_model = request.POST.get('kg_extraction_model', '').strip()
            if name:
                KnowledgeGraph.objects.create(
                    name=name, ollama_host=host or '127.0.0.1',
                    ollama_port=int(port or 11434), embedding_model=emb_model,
                    extraction_model=ext_model,
                )
                knowledge_graphs = KnowledgeGraph.objects.all()
                upload_message = f'知识图谱「{name}」已创建。'
            else:
                upload_message = '知识图谱名称不能为空。'

        elif form_type == 'kg_edit':
            kg = get_object_or_404(KnowledgeGraph, id=kg_id)
            kg.name = request.POST.get('kg_name', kg.name).strip()
            kg.ollama_host = request.POST.get('kg_host', kg.ollama_host).strip()
            kg.ollama_port = int(request.POST.get('kg_port', kg.ollama_port))
            kg.embedding_model = request.POST.get('kg_embedding_model', kg.embedding_model).strip()
            kg.extraction_model = request.POST.get('kg_extraction_model', kg.extraction_model).strip()
            kg.save()
            knowledge_graphs = KnowledgeGraph.objects.all()
            upload_message = f'知识图谱「{kg.name}」已更新。'

        elif form_type == 'kg_delete':
            kg = get_object_or_404(KnowledgeGraph, id=kg_id)
            name = kg.name
            kg.delete()
            knowledge_graphs = KnowledgeGraph.objects.all()
            upload_message = f'知识图谱「{name}」已删除。'

        # ──────────────────────────────────────────
        #  Reference Card CRUD (within a KG)
        # ──────────────────────────────────────────
        elif form_type == 'kg_refcard_create':
            kg = get_object_or_404(KnowledgeGraph, id=kg_id)
            title = request.POST.get('rc_title', '').strip()
            keywords = request.POST.get('rc_keywords', '')
            category = request.POST.get('rc_category', 'general')
            media_type = request.POST.get('rc_media_type', 'none')
            media_url = request.POST.get('rc_media_url', '').strip()
            media_timespan = request.POST.get('rc_media_timespan', '').strip()
            key_points_raw = request.POST.get('rc_key_points', '[]')
            sort_order = int(request.POST.get('rc_sort_order', 0))
            try:
                key_points = json.loads(key_points_raw)
            except json.JSONDecodeError:
                key_points = []
            if title:
                ReferenceCard.objects.create(
                    knowledge_graph=kg, title=title,
                    trigger_keywords=keywords, category=category,
                    media_type=media_type, media_url=media_url,
                    media_timespan=media_timespan, key_points=key_points,
                    sort_order=sort_order,
                )
                upload_message = f'指南卡「{title}」已添加。'
            else:
                upload_message = '标题不能为空。'

        elif form_type == 'kg_refcard_edit':
            card_id = request.POST.get('rc_id')
            card = get_object_or_404(ReferenceCard, id=card_id)
            card.title = request.POST.get('rc_title', card.title).strip()
            card.trigger_keywords = request.POST.get('rc_keywords', card.trigger_keywords)
            card.category = request.POST.get('rc_category', card.category)
            card.media_type = request.POST.get('rc_media_type', card.media_type)
            card.media_url = request.POST.get('rc_media_url', card.media_url).strip()
            card.media_timespan = request.POST.get('rc_media_timespan', card.media_timespan).strip()
            key_points_raw = request.POST.get('rc_key_points', '[]')
            try:
                card.key_points = json.loads(key_points_raw)
            except json.JSONDecodeError:
                pass
            card.sort_order = int(request.POST.get('rc_sort_order', card.sort_order))
            card.save()
            upload_message = f'指南卡「{card.title}」已更新。'

        elif form_type == 'kg_refcard_delete':
            card = get_object_or_404(ReferenceCard, id=request.POST.get('rc_id'))
            title = card.title
            card.delete()
            upload_message = f'指南卡「{title}」已删除。'

        # ──────────────────────────────────────────
        #  Knowledge Entry CRUD
        # ──────────────────────────────────────────
        elif form_type == 'kg_knowledge_create':
            kg = get_object_or_404(KnowledgeGraph, id=kg_id)
            title = request.POST.get('ke_title', '').strip()
            content = request.POST.get('ke_content', '').strip()
            source = request.POST.get('ke_source', '').strip()
            tags = request.POST.get('ke_tags', '').strip()
            if title and content:
                KnowledgeEntry.objects.create(
                    knowledge_graph=kg, title=title, content=content,
                    source=source, tags=tags,
                )
                upload_message = f'知识条目「{title}」已添加。'
            else:
                upload_message = '标题和内容不能为空。'

        elif form_type == 'kg_knowledge_edit':
            entry = get_object_or_404(KnowledgeEntry, id=request.POST.get('ke_id'))
            entry.title = request.POST.get('ke_title', entry.title).strip()
            entry.content = request.POST.get('ke_content', entry.content).strip()
            entry.source = request.POST.get('ke_source', entry.source).strip()
            entry.tags = request.POST.get('ke_tags', entry.tags).strip()
            entry.save()
            upload_message = f'知识条目「{entry.title}」已更新。'

        elif form_type == 'kg_knowledge_delete':
            entry = get_object_or_404(KnowledgeEntry, id=request.POST.get('ke_id'))
            title = entry.title
            entry.delete()
            upload_message = f'知识条目「{title}」已删除。'

        # ──────────────────────────────────────────
        #  Literature Entry CRUD
        # ──────────────────────────────────────────
        elif form_type == 'kg_literature_create':
            kg = get_object_or_404(KnowledgeGraph, id=kg_id)
            title = request.POST.get('lit_title', '').strip()
            authors = request.POST.get('lit_authors', '').strip()
            year = request.POST.get('lit_year', '').strip()
            journal = request.POST.get('lit_journal', '').strip()
            doi = request.POST.get('lit_doi', '').strip()
            abstract = request.POST.get('lit_abstract', '').strip()
            full_text = request.POST.get('lit_full_text', '').strip()
            evidence = request.POST.get('lit_evidence', '').strip()
            if title and abstract:
                LiteratureEntry.objects.create(
                    knowledge_graph=kg, title=title, authors=authors,
                    year=int(year) if year else None, journal=journal,
                    doi=doi, abstract=abstract, full_text=full_text,
                    evidence_level=evidence or '',
                )
                upload_message = f'文献「{title}」已添加。'
            else:
                upload_message = '标题和摘要不能为空。'

        elif form_type == 'kg_literature_edit':
            lit = get_object_or_404(LiteratureEntry, id=request.POST.get('lit_id'))
            lit.title = request.POST.get('lit_title', lit.title).strip()
            lit.authors = request.POST.get('lit_authors', lit.authors).strip()
            lit.year = int(request.POST.get('lit_year')) if request.POST.get('lit_year', '').strip() else None
            lit.journal = request.POST.get('lit_journal', lit.journal).strip()
            lit.doi = request.POST.get('lit_doi', lit.doi).strip()
            lit.abstract = request.POST.get('lit_abstract', lit.abstract).strip()
            lit.full_text = request.POST.get('lit_full_text', lit.full_text).strip()
            lit.evidence_level = request.POST.get('lit_evidence', lit.evidence_level).strip()
            lit.save()
            upload_message = f'文献「{lit.title}」已更新。'

        elif form_type == 'kg_literature_delete':
            lit = get_object_or_404(LiteratureEntry, id=request.POST.get('lit_id'))
            title = lit.title
            lit.delete()
            upload_message = f'文献「{title}」已删除。'

        # ──────────────────────────────────────────
        #  Agent — legacy (old)
        # ──────────────────────────────────────────
        elif form_type == 'agent':
            agent_form = AgentForm(request.POST)
            if agent_form.is_valid():
                host = agent_form.cleaned_data['ollama_host']
                port = agent_form.cleaned_data['ollama_port']
                model_name = agent_form.cleaned_data['ollama_model']
                try:
                    available_models = _fetch_ollama_models(host, port)
                    if model_name not in available_models:
                        upload_message = '智能体保存失败: 当前选择的模型不在该 Ollama 服务可用列表中。'
                    else:
                        agent = agent_form.save(commit=False)
                        agent.agent_type = 'legacy'
                        agent.save()
                        agent_form = AgentForm()
                        agents = Agent.objects.all()
                except Exception as e:
                    upload_message = f'智能体保存失败: 无法连接 Ollama 服务（{type(e).__name__}: {e}）'
            else:
                upload_message = f'智能体保存失败: {agent_form.errors.as_text()}'

        elif form_type == 'agent_edit':
            agent_id = request.POST.get('agent_id')
            agent = get_object_or_404(Agent, id=agent_id)
            edit_form = AgentForm(request.POST, instance=agent)
            if edit_form.is_valid():
                host = edit_form.cleaned_data['ollama_host']
                port = edit_form.cleaned_data['ollama_port']
                model_name = edit_form.cleaned_data['ollama_model']
                try:
                    available_models = _fetch_ollama_models(host, port)
                    if model_name not in available_models:
                        upload_message = '智能体更新失败: 当前选择的模型不在该 Ollama 服务可用列表中。'
                    else:
                        edit_form.save()
                        agents = Agent.objects.all()
                except Exception as e:
                    upload_message = f'智能体更新失败: 无法连接 Ollama 服务（{type(e).__name__}: {e}）'
            else:
                upload_message = f'智能体更新失败: {edit_form.errors.as_text()}'

        # ──────────────────────────────────────────
        #  Agent — knowledge graph (new)
        # ──────────────────────────────────────────
        elif form_type == 'agent_kg':
            agent_kg_form = KnowledgeGraphAgentForm(request.POST)
            if agent_kg_form.is_valid():
                host = agent_kg_form.cleaned_data['ollama_host']
                port = agent_kg_form.cleaned_data['ollama_port']
                model_name = agent_kg_form.cleaned_data['ollama_model']
                try:
                    available_models = _fetch_ollama_models(host, port)
                    if model_name not in available_models:
                        upload_message = '智能体保存失败: 当前选择的模型不在该 Ollama 服务可用列表中。'
                    else:
                        agent = agent_kg_form.save(commit=False)
                        agent.agent_type = 'kg'
                        agent.save()
                        agent_kg_form.save_m2m()
                        agent_kg_form = KnowledgeGraphAgentForm()
                        agents = Agent.objects.all()
                except Exception as e:
                    upload_message = f'智能体保存失败: 无法连接 Ollama 服务（{type(e).__name__}: {e}）'
            else:
                upload_message = f'智能体保存失败: {agent_kg_form.errors.as_text()}'

        elif form_type == 'agent_kg_edit':
            agent_id = request.POST.get('agent_id')
            agent = get_object_or_404(Agent, id=agent_id)
            edit_form = KnowledgeGraphAgentForm(request.POST, instance=agent)
            if edit_form.is_valid():
                host = edit_form.cleaned_data['ollama_host']
                port = edit_form.cleaned_data['ollama_port']
                model_name = edit_form.cleaned_data['ollama_model']
                try:
                    available_models = _fetch_ollama_models(host, port)
                    if model_name not in available_models:
                        upload_message = '智能体更新失败: 当前选择的模型不在该 Ollama 服务可用列表中。'
                    else:
                        edit_form.save()
                        agents = Agent.objects.all()
                except Exception as e:
                    upload_message = f'智能体更新失败: 无法连接 Ollama 服务（{type(e).__name__}: {e}）'
            else:
                upload_message = f'智能体更新失败: {edit_form.errors.as_text()}'

        # ──────────────────────────────────────────
        #  Agent actions
        # ──────────────────────────────────────────
        elif form_type == 'agent_action':
            agent_id = request.POST.get('agent_id')
            action = request.POST.get('action')
            agent = get_object_or_404(Agent, id=agent_id)
            if action == 'toggle_active':
                agent.is_active = not agent.is_active
                agent.save()
            elif action == 'delete':
                agent.delete()
            agents = Agent.objects.all()

        elif form_type == 'agent_set_default':
            agent_id = request.POST.get('agent_id')
            agent = get_object_or_404(Agent, id=agent_id)
            Agent.objects.filter(is_default=True).update(is_default=False)
            agent.is_default = True
            agent.save()
            agents = Agent.objects.all()
            upload_message = f'「{agent.name}」已设为默认智能体。'

        # ──────────────────────────────────────────
        #  Single model config (unchanged)
        # ──────────────────────────────────────────
        elif form_type == 'single_model_config':
            host = request.POST.get('ollama_host', '').strip()
            port = request.POST.get('ollama_port', '').strip()
            previous_config = _load_single_model_config()
            try:
                available_models = _fetch_ollama_models(host, port)
                if not available_models:
                    single_model_config = previous_config
                    upload_message = '单模型配置保存失败: 已连接 Ollama，但未获取到任何模型，已保留原配置。'
                else:
                    _save_single_model_config(host, port)
                    single_model_config = _load_single_model_config()
                    upload_message = '单模型配置已更新。'
            except Exception as e:
                single_model_config = previous_config
                upload_message = f'单模型配置保存失败: 无法连接 Ollama 服务（{type(e).__name__}: {e}），已保留原配置。'

        # Refresh KG related data after any mutation
        knowledge_graphs = KnowledgeGraph.objects.all()
        for kg in knowledge_graphs:
            ref_cards_by_kg[kg.id] = kg.reference_cards.filter(is_active=True)
            kg_entries_by_kg[kg.id] = kg.knowledge_entries.filter(is_active=True)
            lit_entries_by_kg[kg.id] = kg.literature.filter(is_active=True)

    return render(request, 'promotions/setup.html', {
        'active_tab': 'setup',
        'role_text': role_text,
        'knowledge_text': knowledge_text,
        'agent_form': agent_form,
        'agent_kg_form': agent_kg_form,
        'agents': agents,
        'single_model_config': single_model_config,
        'upload_message': upload_message,
        # ── Knowledge Graph context ──
        'knowledge_graphs': knowledge_graphs,
        'ref_cards_by_kg': ref_cards_by_kg,
        'kg_entries_by_kg': kg_entries_by_kg,
        'lit_entries_by_kg': lit_entries_by_kg,
        # ── Category & evidence choices for forms ──
        'card_categories': ReferenceCard.CATEGORY_CHOICES,
        'media_types': ReferenceCard.MEDIA_CHOICES,
        'evidence_levels': LiteratureEntry.EVIDENCE_CHOICES,
    })


def promotion_view(request):
    if 'chat_history' not in request.session:
        request.session['chat_history'] = []

    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    chat_history = []

    if request.method == 'POST' and is_ajax:
        started_ns = time.perf_counter_ns()
        started_at = timezone.now()
        agents = Agent.objects.filter(is_active=True).order_by('name')
        model_choices = _get_runtime_model_choices()
        form = PromotionForm(request.POST, agent_choices=agents, model_choices=model_choices)
        if not form.is_valid():
            return JsonResponse({'error': form.errors}, status=400)

        timer = QATimer(request, form.cleaned_data['model_select'], started_ns, started_at)
        try:
            base_path = _get_base_path()
            default_role_text, default_knowledge_text = load_role_and_knowledge(base_path)
            promotion = form.cleaned_data['promotion_text']
            model = form.cleaned_data['model_select']
            patient = _get_session_patient(request)
            error_text = ''
            result = 'success'

            try:
                chat_target = _get_chat_target(model)
                timer.model_name = chat_target['model_name']
                role_text = chat_target['role_text'] if chat_target['role_text'] is not None else default_role_text
                knowledge_text = chat_target['knowledge_text'] if chat_target['knowledge_text'] is not None else default_knowledge_text
                knowledge_text = _append_patient_context(request, knowledge_text)
                system_context = _build_system_context(role_text, knowledge_text)
                session_path = _get_session_context_path(request, model)
                session_data = _load_session_context(session_path, system_context, model)
                session_data['system_context'] = system_context

                response_text = ''
                completed = False
                think_state = {'in_think': False}
                with timer.track_response(_stream_ollama_response_by_target(
                    chat_target['base_url'],
                    chat_target['model_name'],
                    _build_messages(system_context, _history_messages_for_ollama(session_data), promotion),
                )) as response:
                    response.raise_for_status()
                    for line in response.iter_lines(decode_unicode=True):
                        if timer.cancelled:
                            break
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if data.get('error'):
                            raise RuntimeError('模型服务返回错误')
                        message = data.get('message', {}) or {}
                        content = message.get('content', '')
                        _, answer = _split_thinking_delta(content, think_state)
                        timer.first_answer(answer)
                        response_text += answer
                        if data.get('done') is True:
                            completed = True
                            break
                if timer.cancelled:
                    result, error_text = 'cancelled', '用户已停止回答'
                elif not completed:
                    result, error_text = 'interrupted', '模型连接提前结束'
                elif timer.first_answer_ms is None:
                    result, error_text = 'failed', '模型未返回答案'
            except Exception as e:
                result = 'cancelled' if timer.cancelled else failure_status(e)
                response_text = f'（服务端错误）{type(e).__name__}: {e}'
                error_text = f'{type(e).__name__}: {e}'
                session_data = None
                session_path = None

            if result == 'success' and session_data is not None and session_path is not None:
                _append_session_message(session_data, 'user', promotion)
                _append_session_message(session_data, 'assistant', response_text)
                _save_session_context(session_path, session_data)

            chat_history = _append_chat_history(request, promotion, response_text, model)
            if result == 'success':
                _save_latest_response_text(response_text)
            # 问答入库（数据分析数据源）
            record = log_qa_record(
                request=request, source='web', user=request.user, patient=patient,
                model_key=model, question=promotion, answer=response_text,
                latency_ms=timer.elapsed_ms(), error=error_text,
            )

            timer.finish(result, record)
            return JsonResponse({'chat_history': chat_history, 'audio_url': None, 'result': result,
                                 'error': error_text, 'timing_id': str(timer.timing_id) if timer.timing_id else None})
        finally:
            timer.finish('failed')

    agents = Agent.objects.filter(is_active=True).order_by('name')
    model_choices = _get_runtime_model_choices()
    form = PromotionForm(agent_choices=agents, model_choices=model_choices)

    # 患者抽屉：在院患者列表 + 当前关联患者
    patients = Patient.objects.filter(is_active=True).order_by('bed_no', 'name')
    current_patient = _get_session_patient(request)
    default_agent = agents.first()
    default_model = f'agent:{default_agent.slug}' if default_agent else (model_choices[0][0] if model_choices else '')
    chat_history = _selected_patient_history(request, default_model)

    return render(request, 'promotions/promotion.html', {
        'form': form,
        'chat_history': chat_history,
        'show_intro': True,
        'agent_options': [(f'agent:{agent.slug}', f'智能体（{agent.name}）') for agent in agents],
        'model_options': model_choices,
        'debug_session_key': _ensure_request_session_key(request),
        'debug_runtime_sessions_dir': str(RUNTIME_SESSIONS_DIR),
        'patients': patients,
        'current_patient': current_patient,
    })


@ensure_csrf_cookie
def promotion_stream(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)

    started_ns = time.perf_counter_ns()
    started_at = timezone.now()
    agents = Agent.objects.filter(is_active=True).order_by('name')
    model_choices = _get_runtime_model_choices()
    form = PromotionForm(request.POST, agent_choices=agents, model_choices=model_choices)
    if not form.is_valid():
        return JsonResponse({'error': form.errors}, status=400)

    base_path = _get_base_path()
    default_role_text, default_knowledge_text = load_role_and_knowledge(base_path)
    promotion = form.cleaned_data['promotion_text']
    model = form.cleaned_data['model_select']
    patient = _get_session_patient(request)
    timer = QATimer(request, model, started_ns, started_at)

    def generate():
        full_text = ''
        think_text = ''
        think_state = {'in_think': False}
        session_path = None
        session_data = None
        error_text = ''
        result = 'success'
        completed = False

        try:
            yield helper_sse('status', {
                'message': 'thinking', 'timing_id': str(timer.timing_id) if timer.timing_id else None,
            })
            if timer.cancelled:
                raise InterruptedError('用户已停止回答')
            chat_target = _get_chat_target(model)
            timer.model_name = chat_target['model_name']
            role_text = chat_target['role_text'] if chat_target['role_text'] is not None else default_role_text
            knowledge_text = chat_target['knowledge_text'] if chat_target['knowledge_text'] is not None else default_knowledge_text

            # ── RAG: augment knowledge for KG agents ──
            if model.startswith('agent:'):
                try:
                    slug = model.split(':', 1)[1]
                    agent = Agent.objects.get(slug=slug, is_active=True)
                    rag_result = route_and_retrieve(agent, promotion)
                    if rag_result and rag_result.get('context'):
                        knowledge_text = (knowledge_text or '') + '\n\n' + rag_result['context']
                except Agent.DoesNotExist:
                    pass

            # ── 患者信息注入：关联患者后供 AI 参考 ──
            knowledge_text = _append_patient_context(request, knowledge_text)

            system_context = _build_system_context(role_text, knowledge_text)
            session_path = _get_session_context_path(request, model)
            session_data = _load_session_context(session_path, system_context, model)
            session_data['system_context'] = system_context

            if timer.cancelled:
                raise InterruptedError('用户已停止回答')
            with timer.track_response(_stream_ollama_response_by_target(
                chat_target['base_url'],
                chat_target['model_name'],
                _build_messages(system_context, _history_messages_for_ollama(session_data), promotion),
            )) as response:
                response.raise_for_status()
                for line in response.iter_lines(decode_unicode=True):
                    if timer.cancelled:
                        break
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if data.get('error'):
                        raise RuntimeError('模型服务返回错误')
                    message = data.get('message', {}) or {}

                    # Newer Ollama thinking API: reasoning is streamed via message.thinking
                    direct_thinking_delta = message.get('thinking', '')
                    if direct_thinking_delta:
                        think_text += direct_thinking_delta
                        yield helper_sse('thinking', {'content': direct_thinking_delta})

                    # Final answer text is still streamed via message.content
                    delta = message.get('content', '')

                    # Backward compatibility: some models / setups may still emit <think>...</think>
                    if delta:
                        thinking_delta, answer_delta = _split_thinking_delta(delta, think_state)
                    else:
                        thinking_delta, answer_delta = '', ''

                    if thinking_delta:
                        think_text += thinking_delta
                        yield helper_sse('thinking', {'content': thinking_delta})

                    if answer_delta:
                        timer.first_answer(answer_delta)
                        full_text += answer_delta
                        yield helper_sse('delta', {'content': answer_delta})

                    if data.get('done') is True:
                        completed = True
                        break
            if timer.cancelled:
                result, error_text = 'cancelled', '用户已停止回答'
            elif not completed:
                result, error_text = 'interrupted', '模型连接提前结束'
            elif timer.first_answer_ms is None:
                result, error_text = 'failed', '模型未返回答案'

        except GeneratorExit:
            result = 'cancelled' if timer.cancelled else 'interrupted'
            error_text = '用户已停止回答' if timer.cancelled else '客户端连接已断开'
            record = log_qa_record(
                request=request, source='web', user=request.user, patient=patient,
                model_key=model, question=promotion, answer=full_text, thinking=think_text,
                latency_ms=timer.elapsed_ms(), error=error_text,
            )
            timer.finish(result, record)
            raise
        except Exception as e:
            result = 'cancelled' if timer.cancelled else failure_status(e)
            error_text = '用户已停止回答' if timer.cancelled else f'（服务端错误）{type(e).__name__}: {e}'

        if result == 'success' and session_data is not None and session_path is not None:
            _append_session_message(session_data, 'user', promotion)
            _append_session_message(session_data, 'assistant', full_text)
            _save_session_context(session_path, session_data)

        if result == 'success':
            _append_chat_history(request, promotion, full_text, model)
        # 问答入库（数据分析数据源）
        record = log_qa_record(
            request=request, source='web', user=request.user, patient=patient,
            model_key=model, question=promotion, answer=full_text, thinking=think_text,
            latency_ms=timer.elapsed_ms(), error=error_text,
        )
        timer.finish(result, record)
        yield helper_sse('done', {
            'final': full_text, 'thinking': think_text, 'audio_url': None, 'result': result, 'error': error_text,
        })

    response = StreamingHttpResponse(TimedStream(generate(), timer), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


# ══════════════════════════════════════════════════════════════
#  二期升级：患者管理 / 数据分析 / AI 驱动配置
# ══════════════════════════════════════════════════════════════

def _parse_date(value):
    """解析 date input 'YYYY-MM-DD'，失败返回 None"""
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


def _parse_datetime_local(value):
    """解析 datetime-local 'YYYY-MM-DDTHH:mm'，返回 aware datetime，失败返回 None"""
    if not value:
        return None
    try:
        naive = datetime.fromisoformat(str(value).strip().replace('T', ' '))
        return timezone.make_aware(naive)
    except ValueError:
        return None


def _parse_surgery_plans_json(raw):
    """解析手术计划 JSON 列表，非法行静默丢弃（与 rc_key_points 容错风格一致）"""
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, list):
        return None
    plans = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        planned = _parse_datetime_local(item.get('planned_time'))
        if planned is None:
            continue
        plans.append({'planned_time': planned, 'note': str(item.get('note', ''))[:200]})
    return plans


# ──────────────────────────────────────────────────────────────
#  患者选择（聊天页左抽屉）
# ──────────────────────────────────────────────────────────────

def patient_select_view(request):
    """POST /promotion/patient/select/ — 关联/清除当前会话患者"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, ValueError):
        body = {}
    patient_id = body.get('patient_id')
    if patient_id in (None, ''):
        request.session.pop('current_patient_id', None)
        return JsonResponse({'ok': True, 'patient': None,
                             'history': _selected_patient_history(request, body.get('model_key'))})
    patient = Patient.objects.filter(id=patient_id, is_active=True).first()
    if patient is None:
        return JsonResponse({'ok': False, 'error': '患者不存在或已归档'}, status=404)
    request.session['current_patient_id'] = patient.id
    return JsonResponse({'ok': True, 'patient': {
        'id': patient.id, 'bed_no': patient.bed_no, 'name': patient.name,
    }, 'history': _selected_patient_history(request, body.get('model_key'))})


# ──────────────────────────────────────────────────────────────
#  患者管理页（CRUD，form_type POST + 整页重渲染，沿用 setup 惯例）
# ──────────────────────────────────────────────────────────────

def patients_view(request):
    upload_message = None
    disease_filter = request.GET.get('disease', '')
    show_archived = request.GET.get('archived') == '1'

    if request.method == 'POST':
        form_type = request.POST.get('form_type')
        if form_type in ('patient_create', 'patient_edit'):
            patient_id = request.POST.get('patient_id')
            bed_no = (request.POST.get('bed_no') or '').strip()
            name = (request.POST.get('name') or '').strip()
            gender = request.POST.get('gender', '')
            if gender not in ('male', 'female'):
                gender = ''
            birth_date = _parse_date(request.POST.get('birth_date'))
            disease_type = request.POST.get('disease_type', 'other')
            if disease_type not in dict(DISEASE_TYPE_CHOICES):
                disease_type = 'other'
            admission_time = _parse_datetime_local(request.POST.get('admission_time'))
            cautions = request.POST.get('cautions', '').strip()
            notes = request.POST.get('notes', '').strip()

            try:
                stay_min_raw = request.POST.get('expected_stay_min_days', '').strip()
                stay_max_raw = request.POST.get('expected_stay_max_days', '').strip()
                stay_min = int(stay_min_raw) if stay_min_raw else None
                stay_max = int(stay_max_raw) if stay_max_raw else None
            except ValueError:
                stay_min = stay_max = None

            patient = None
            if not bed_no or not name:
                upload_message = {'type': 'error', 'text': '床号和姓名为必填项'}
            elif stay_min is not None and stay_max is not None and stay_min > stay_max:
                upload_message = {'type': 'error', 'text': '预计在院最短天数不能大于最长天数'}
            elif form_type == 'patient_create':
                patient = Patient.objects.create(
                    bed_no=bed_no, name=name, gender=gender, birth_date=birth_date,
                    disease_type=disease_type, admission_time=admission_time,
                    expected_stay_min_days=stay_min, expected_stay_max_days=stay_max,
                    cautions=cautions, notes=notes,
                )
                upload_message = {'type': 'success', 'text': f'患者「{bed_no}床 {name}」已创建'}
            else:
                patient = Patient.objects.filter(id=patient_id).first()
                if patient is None:
                    upload_message = {'type': 'error', 'text': '患者不存在'}
                else:
                    patient.bed_no = bed_no
                    patient.name = name
                    patient.gender = gender
                    patient.birth_date = birth_date
                    patient.disease_type = disease_type
                    patient.admission_time = admission_time
                    patient.expected_stay_min_days = stay_min
                    patient.expected_stay_max_days = stay_max
                    patient.cautions = cautions
                    patient.notes = notes
                    patient.save()
                    upload_message = {'type': 'success', 'text': f'患者「{bed_no}床 {name}」已更新'}

            plans = _parse_surgery_plans_json(request.POST.get('surgery_plans_json'))
            if plans is not None and patient is not None:
                patient.surgery_plans.all().delete()
                SurgeryPlan.objects.bulk_create(
                    SurgeryPlan(patient=patient, **plan) for plan in plans
                )
        elif form_type == 'patient_delete':
            patient = Patient.objects.filter(id=request.POST.get('patient_id')).first()
            if patient is not None:
                patient.is_active = False
                patient.save(update_fields=['is_active'])
                upload_message = {'type': 'success', 'text': f'患者「{patient.bed_no}床 {patient.name}」已归档（历史问答保留）'}
        elif form_type == 'patient_restore':
            patient = Patient.objects.filter(id=request.POST.get('patient_id')).first()
            if patient is not None:
                patient.is_active = True
                patient.save(update_fields=['is_active'])
                upload_message = {'type': 'success', 'text': f'患者「{patient.bed_no}床 {patient.name}」已恢复在院'}

    patients = Patient.objects.all().order_by('-is_active', 'bed_no', 'name')
    if disease_filter:
        patients = patients.filter(disease_type=disease_filter)
    if not show_archived:
        patients = patients.filter(is_active=True)

    # 编辑模态回填：患者手术计划（datetime-local 格式字符串）
    plans_map = {
        str(p.id): [
            {'t': timezone.localtime(sp.planned_time).strftime('%Y-%m-%dT%H:%M'), 'note': sp.note}
            for sp in p.surgery_plans.all()
        ]
        for p in patients
    }

    return render(request, 'promotions/patients.html', {
        'patients': patients,
        'plans_map': plans_map,
        'disease_choices': DISEASE_TYPE_CHOICES,
        'disease_filter': disease_filter,
        'show_archived': show_archived,
        'upload_message': upload_message,
        'active_tab': 'patients',
    })


# ──────────────────────────────────────────────────────────────
#  数据分析页
# ──────────────────────────────────────────────────────────────

def _mask_name(name):
    """姓名脱敏：张三 → 张*"""
    if not name:
        return ''
    return name[0] + '*'


def _build_qa_base_qs(start, end, include_failures=False):
    """带时间窗的基准查询集；成功指标与失败指标共用筛选条件。"""
    qs = QARecord.objects.all()
    if not include_failures:
        qs = qs.exclude(error__gt='')
    if start:
        qs = qs.filter(created_at__date__gte=start)
    if end:
        qs = qs.filter(created_at__date__lte=end)
    return qs


def _apply_topic_filter(qs, topic):
    """按主题过滤。SQLite 不支持 JSONField 数组 contains 查询，改为 Python 侧过滤
    （先经日期等其他条件裁剪，病区单月量级足够；数据量再大时迁移 PostgreSQL）"""
    if not topic:
        return qs
    matching_ids = [
        row_id for row_id, topics in qs.values_list('id', 'topics')
        if topics and topic in topics
    ]
    return qs.filter(id__in=matching_ids)


def _apply_qa_filters(qs, patient_id=None, disease='', topic='', q=''):
    if patient_id:
        qs = qs.filter(patient_id=patient_id)
    if disease:
        qs = qs.filter(patient__disease_type=disease)
    qs = _apply_topic_filter(qs, topic)
    if q:
        qs = qs.filter(question__icontains=q)
    return qs


def analytics_view(request):
    """GET /promotion/analytics/ — 数据分析页"""
    pending_count = QARecord.objects.filter(
        extraction_status__in=('pending', 'error')
    ).count()
    return render(request, 'promotions/analytics.html', {
        'active_tab': 'analytics',
        'ai_config': get_active_config(),
        'pending_count': pending_count,
        'patients': Patient.objects.filter(is_active=True).order_by('bed_no', 'name'),
        'disease_choices': DISEASE_TYPE_CHOICES,
        'topic_choices': TOPIC_WHITELIST,
        'default_start': (timezone.localdate() - timedelta(days=29)).isoformat(),
        'default_end': timezone.localdate().isoformat(),
    })


def analytics_data_view(request):
    """GET /promotion/analytics/data/ — 统计聚合 JSON"""
    try:
        start = _parse_date(request.GET.get('start'))
        end = _parse_date(request.GET.get('end'))
        if start is None:
            start = timezone.localdate() - timedelta(days=29)
        if end is None:
            end = timezone.localdate()
        if start > end:
            start, end = end, start
        patient_id = request.GET.get('patient_id')
        if patient_id:
            patient_id = int(patient_id)
        disease = request.GET.get('disease', '')
        topic = request.GET.get('topic', '')
        q = (request.GET.get('q') or '').strip()
        page = max(int(request.GET.get('page', 1) or 1), 1)
        page_size = min(max(int(request.GET.get('page_size', 20) or 20), 1), 100)
    except ValueError:
        return JsonResponse({'ok': False, 'error': '参数格式错误'}, status=400)

    filtered = _apply_qa_filters(
        _build_qa_base_qs(start, end, include_failures=True),
        patient_id=patient_id, disease=disease, topic=topic, q=q,
    )
    qs = filtered.exclude(error__gt='')

    # ── 指标磁贴 ──
    total = qs.count()
    today = qs.filter(created_at__date=timezone.localdate()).count()
    patient_linked = qs.exclude(patient__isnull=True).count()
    avg_latency_ms = None
    latencies = list(qs.exclude(latency_ms__isnull=True).order_by('-created_at')[:2000]
                     .values_list('latency_ms', flat=True))
    if latencies:
        avg_latency_ms = int(sum(latencies) / len(latencies))
    pending_count = qs.filter(extraction_status__in=('pending', 'error')).count()
    failed_count = filtered.filter(error__gt='').count()

    # ── 每日趋势（补齐连续日期；全部 + 关联患者两个系列）──
    from django.db.models import Count
    from django.db.models.functions import TruncDate
    trend_rows = (
        qs.annotate(day=TruncDate('created_at'))
        .values('day').annotate(count=Count('id')).order_by('day')
    )
    patient_trend_rows = (
        qs.exclude(patient__isnull=True)
        .annotate(day=TruncDate('created_at'))
        .values('day').annotate(count=Count('id')).order_by('day')
    )
    trend_map = {row['day'].isoformat(): row['count'] for row in trend_rows}
    patient_trend_map = {row['day'].isoformat(): row['count'] for row in patient_trend_rows}
    trend = []
    cursor = start
    while cursor <= end and len(trend) < 366:
        iso = cursor.isoformat()
        trend.append({
            'date': iso,
            'count': trend_map.get(iso, 0),
            'patient_count': patient_trend_map.get(iso, 0),
        })
        cursor += timedelta(days=1)

    # ── 主题 / 关键词 TOP（SQLite 平铺 Counter，窗口限 5000 条；
    #    院内单日量级足够，数据量大迁移 PostgreSQL 后可放宽）──
    from collections import Counter
    window = qs.order_by('-created_at')[:5000]
    topic_counter = Counter(
        t for topics in window.values_list('topics', flat=True) for t in (topics or [])
    )
    keyword_counter = Counter(
        k for keywords in window.values_list('keywords', flat=True) for k in (keywords or [])
    )
    topics = [{'name': k, 'count': v} for k, v in topic_counter.most_common(10)]
    keywords = [{'name': k, 'count': v} for k, v in keyword_counter.most_common(10)]

    # ── 高频问题 TOP15 ──
    from django.db.models import Max
    top_questions = list(
        qs.values('question')
        .annotate(count=Count('id'), last_at=Max('created_at'))
        .order_by('-count', '-last_at')[:15]
    )
    for item in top_questions:
        if item['last_at']:
            item['last_at'] = timezone.localtime(item['last_at']).isoformat(sep=' ', timespec='minutes')

    # ── 明细（分页）──
    total_records = qs.count()
    records = []
    for rec in qs.select_related('patient', 'user', 'agent').order_by('-created_at')[
        (page - 1) * page_size: page * page_size
    ]:
        records.append({
            'id': rec.id,
            'created_at': timezone.localtime(rec.created_at).isoformat(sep=' ', timespec='minutes'),
            'source': rec.source,
            'patient': (
                {'bed_no': rec.patient.bed_no, 'name': _mask_name(rec.patient.name),
                 'disease': rec.patient.get_disease_type_display()}
                if rec.patient else None
            ),
            'model_key': rec.model_key,
            'question': rec.question[:100],
            'answer': rec.answer[:200],
            'keywords': rec.keywords or [],
            'topics': rec.topics or [],
            'extraction_status': rec.extraction_status,
            'latency_ms': rec.latency_ms,
        })

    return JsonResponse({'ok': True, 'data': {
        'tiles': {
            'total': total,
            'today': today,
            'patient_linked': patient_linked,
            'avg_latency_ms': avg_latency_ms,
            'pending_extraction': pending_count,
            'failed_count': failed_count,
        },
        'trend': trend,
        'topics': topics,
        'keywords': keywords,
        'top_questions': top_questions,
        'records': records,
        'pagination': {'page': page, 'page_size': page_size, 'total': total_records},
        'filters': {'start': start.isoformat(), 'end': end.isoformat(),
                    'patient_id': patient_id or None, 'disease': disease, 'topic': topic, 'q': q},
    }})


def analytics_extract_view(request):
    """POST /promotion/analytics/extract/ — 手动批量提取（最多 30 条/次）"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)

    # 卡死的 processing（超过 30 分钟）重置为 pending，避免永远跳过
    stale_cutoff = timezone.now() - timedelta(minutes=30)
    QARecord.objects.filter(
        extraction_status='processing', created_at__lte=stale_cutoff
    ).update(extraction_status='pending')

    records = list(
        QARecord.objects.filter(extraction_status__in=('pending', 'error'))
        .order_by('created_at')[:30]
    )
    done = 0
    failed = 0
    results = []
    for rec in records:
        outcome = extract_record(rec.id)
        if outcome.get('ok'):
            done += 1
        else:
            failed += 1
        results.append({
            'id': rec.id,
            'question': rec.question[:40],
            'method': outcome.get('method', ''),
            'keywords': outcome.get('keywords', []),
            'topics': outcome.get('topics', []),
            'error': outcome.get('error', ''),
        })
    remaining = QARecord.objects.filter(extraction_status__in=('pending', 'error')).count()
    return JsonResponse({'ok': True, 'done': done, 'failed': failed,
                         'remaining': remaining, 'results': results})


def _build_insight_summary(start, end, qs, failed_count=0):
    """把统计摘要裁剪成 AI 洞察的输入文本"""
    from collections import Counter
    from django.db.models import Count

    total = qs.count()
    today = qs.filter(created_at__date=timezone.localdate()).count()
    patient_linked = qs.exclude(patient__isnull=True).count()

    window = qs.order_by('-created_at')[:3000]
    topic_counter = Counter(
        t for topics in window.values_list('topics', flat=True) for t in (topics or [])
    )
    keyword_counter = Counter(
        k for keywords in window.values_list('keywords', flat=True) for k in (keywords or [])
    )
    top_questions = list(
        qs.values('question').annotate(count=Count('id')).order_by('-count')[:10]
    )
    disease_counter = Counter(
        disease for _, disease in qs.exclude(patient__isnull=True).order_by()
        .values_list('patient_id', 'patient__disease_type').distinct()
    )
    samples = []
    for rec in qs.select_related('patient').order_by('-created_at')[:5]:
        samples.append(f'Q: {rec.question[:120]}\nA: {rec.answer[:200]}')

    lines = [
        f'统计区间: {start} 至 {end}',
        f'总问答数: {total}；今日新增: {today}；关联患者问答: {patient_linked}；服务端失败记录: {failed_count}',
        '主题分布 TOP10: ' + ('；'.join(f'{k} {v}次' for k, v in topic_counter.most_common(10)) or '无'),
        '关键词 TOP10: ' + ('；'.join(f'{k} {v}次' for k, v in keyword_counter.most_common(10)) or '无'),
        '高频问题 TOP10: ' + ('；'.join(
            f'{i + 1}) {item["question"][:50]}({item["count"]}次)'
            for i, item in enumerate(top_questions)
        ) or '无'),
        '关联患者疾病分布: ' + ('；'.join(
            f'{dict(DISEASE_TYPE_CHOICES).get(k, k)} {v}人' for k, v in disease_counter.most_common()
        ) or '无'),
        '近期典型问答:',
    ]
    lines.extend(samples or ['（暂无）'])
    return '\n'.join(lines)


INSIGHT_SYSTEM_PROMPT = (
    '你是一名医院病区护理数据分析师，面向护士长输出护理质量分析简报。'
    '基于给定的统计数据，用中文 Markdown 输出以下小节：'
    '## 总体概况 / ## 患者关切热点 / ## 趋势与变化 / ## 护理提示与宣教建议（结合功能神经外科：'
    '帕金森DBS、癫痫、肌张力障碍等）/ ## 风险关注。'
    '客观引用数据，不要编造数字；总字数控制在 600 字内。'
)


def analytics_insight_view(request):
    """POST /promotion/analytics/insight/ — AI 洞察简报（使用当前过滤器）"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)
    config = get_active_config()
    if config is None:
        return JsonResponse({'ok': False, 'error': '请先在页面顶部完成 AI 驱动配置'}, status=400)
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, ValueError):
        body = {}
    start = _parse_date(body.get('start'))
    end = _parse_date(body.get('end'))
    if start is None:
        start = timezone.localdate() - timedelta(days=29)
    if end is None:
        end = timezone.localdate()
    if start > end:
        start, end = end, start

    try:
        filtered = _apply_qa_filters(
            _build_qa_base_qs(start, end, include_failures=True),
            patient_id=body.get('patient_id'), disease=body.get('disease', ''),
            topic=body.get('topic', ''), q=(body.get('q') or '').strip(),
        )
        qs = filtered.exclude(error__gt='')
        summary = _build_insight_summary(start, end, qs, filtered.filter(error__gt='').count())
    except (ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': '参数格式错误'}, status=400)
    t0 = time.perf_counter()
    try:
        text = chat_complete_text(
            cfg=config, system=INSIGHT_SYSTEM_PROMPT, user_text=summary, temperature=0.3,
        )
    except AIClientError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=502)
    return JsonResponse({'ok': True, 'text': text, 'model': config.model,
                         'elapsed_ms': int((time.perf_counter() - t0) * 1000)})


# ──────────────────────────────────────────────────────────────
#  AI 驱动配置（数据分析页顶部）
# ──────────────────────────────────────────────────────────────

def ai_fetch_models_view(request):
    """POST /promotion/analytics/ai/fetch-models/ — 按服务类型获取模型列表"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, ValueError):
        body = {}
    provider_type = body.get('provider_type', 'ollama')
    if provider_type not in dict(AIConfig.PROVIDER_CHOICES):
        return JsonResponse({'ok': False, 'error': '未知的服务类型'}, status=400)
    ok, models_or_error = fetch_models(provider_type, body.get('base_url', ''), body.get('api_key', ''))
    if not ok:
        return JsonResponse({'ok': False, 'error': models_or_error}, status=502)
    return JsonResponse({'ok': True, 'models': models_or_error})


def ai_save_config_view(request):
    """POST /promotion/analytics/ai/save-config/ — 保存配置（单活跃策略，允许离线保存）"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, ValueError):
        body = {}
    provider_type = body.get('provider_type', 'ollama')
    if provider_type not in dict(AIConfig.PROVIDER_CHOICES):
        return JsonResponse({'ok': False, 'error': '未知的服务类型'}, status=400)
    base_url = (body.get('base_url') or '').strip()
    model = (body.get('model') or '').strip()
    if not base_url:
        return JsonResponse({'ok': False, 'error': '请填写 API 地址'}, status=400)
    if not model:
        return JsonResponse({'ok': False, 'error': '请选择或填写模型名'}, status=400)
    AIConfig.objects.filter(is_active=True).update(is_active=False)
    AIConfig.objects.create(
        provider_type=provider_type, base_url=base_url,
        api_key=(body.get('api_key') or '').strip(), model=model, is_active=True,
    )
    return JsonResponse({'ok': True})
