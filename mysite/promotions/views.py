from pathlib import Path
import json
import re
from datetime import datetime

import requests
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import ensure_csrf_cookie

from .forms import AgentForm, KnowledgeGraphAgentForm, PromotionForm
from .models import (
    Agent, KnowledgeDocument, KnowledgeGraph,
    ReferenceCard, KnowledgeEntry, LiteratureEntry,
)
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
    return requests.post(f'{base_url}/api/chat', json=payload, stream=True)


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


def _append_chat_history(request, promotion: str, response_text: str):
    chat_history = request.session.get('chat_history', [])
    chat_history.append({'user': promotion, 'response': response_text})
    request.session['chat_history'] = chat_history
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


def _safe_model_key(model_key: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]+', '_', model_key or 'default')


def _get_session_context_path(request, model_key: str) -> Path:
    session_key = _ensure_request_session_key(request)
    RUNTIME_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return RUNTIME_SESSIONS_DIR / f'{session_key}__{_safe_model_key(model_key)}.json'


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
        agents = Agent.objects.filter(is_active=True).order_by('name')
        model_choices = _get_runtime_model_choices()
        form = PromotionForm(request.POST, agent_choices=agents, model_choices=model_choices)
        if not form.is_valid():
            return JsonResponse({'error': form.errors}, status=400)

        base_path = _get_base_path()
        default_role_text, default_knowledge_text = load_role_and_knowledge(base_path)
        promotion = form.cleaned_data['promotion_text']
        model = form.cleaned_data['model_select']

        try:
            chat_target = _get_chat_target(model)
            role_text = chat_target['role_text'] if chat_target['role_text'] is not None else default_role_text
            knowledge_text = chat_target['knowledge_text'] if chat_target['knowledge_text'] is not None else default_knowledge_text
            system_context = _build_system_context(role_text, knowledge_text)
            session_path = _get_session_context_path(request, model)
            session_data = _load_session_context(session_path, system_context, model)
            session_data['system_context'] = system_context

            response_text = ''
            with _stream_ollama_response_by_target(
                chat_target['base_url'],
                chat_target['model_name'],
                _build_messages(system_context, _history_messages_for_ollama(session_data), promotion),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    message = data.get('message', {}) or {}
                    response_text += message.get('content', '')
                    if data.get('done', True):
                        break
        except Exception as e:
            response_text = f'（服务端错误）{type(e).__name__}: {e}'
            session_data = None
            session_path = None

        if session_data is not None and session_path is not None:
            _append_session_message(session_data, 'user', promotion)
            _append_session_message(session_data, 'assistant', response_text)
            _save_session_context(session_path, session_data)

        chat_history = _append_chat_history(request, promotion, response_text)
        _save_latest_response_text(response_text)

        return JsonResponse({'chat_history': chat_history, 'audio_url': None})

    agents = Agent.objects.filter(is_active=True).order_by('name')
    model_choices = _get_runtime_model_choices()
    form = PromotionForm(agent_choices=agents, model_choices=model_choices)
    if 'chat_history' in request.session:
        del request.session['chat_history']
    _clear_request_session_contexts(request)

    return render(request, 'promotions/promotion.html', {
        'form': form,
        'chat_history': chat_history,
        'show_intro': True,
        'agent_options': [(f'agent:{agent.slug}', f'智能体（{agent.name}）') for agent in agents],
        'model_options': model_choices,
        'debug_session_key': _ensure_request_session_key(request),
        'debug_runtime_sessions_dir': str(RUNTIME_SESSIONS_DIR),
    })


@ensure_csrf_cookie
def promotion_stream(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)

    agents = Agent.objects.filter(is_active=True).order_by('name')
    model_choices = _get_runtime_model_choices()
    form = PromotionForm(request.POST, agent_choices=agents, model_choices=model_choices)
    if not form.is_valid():
        return JsonResponse({'error': form.errors}, status=400)

    base_path = _get_base_path()
    default_role_text, default_knowledge_text = load_role_and_knowledge(base_path)
    promotion = form.cleaned_data['promotion_text']
    model = form.cleaned_data['model_select']

    def generate():
        yield helper_sse('status', {'message': 'thinking'})

        full_text = ''
        think_text = ''
        think_state = {'in_think': False}
        session_path = None
        session_data = None

        try:
            chat_target = _get_chat_target(model)
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

            system_context = _build_system_context(role_text, knowledge_text)
            session_path = _get_session_context_path(request, model)
            session_data = _load_session_context(session_path, system_context, model)
            session_data['system_context'] = system_context

            with _stream_ollama_response_by_target(
                chat_target['base_url'],
                chat_target['model_name'],
                _build_messages(system_context, _history_messages_for_ollama(session_data), promotion),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

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
                        full_text += answer_delta
                        yield helper_sse('delta', {'content': answer_delta})
                    elif delta and not direct_thinking_delta:
                        # If no tagged-think split happened, treat plain content as final answer
                        full_text += delta
                        yield helper_sse('delta', {'content': delta})

                    if data.get('done', True):
                        break

        except Exception as e:
            err = f'（服务端错误）{type(e).__name__}: {e}'
            yield helper_sse('delta', {'content': err})
            full_text = (full_text or '') + '\n' + err

        if session_data is not None and session_path is not None:
            _append_session_message(session_data, 'user', promotion)
            _append_session_message(session_data, 'assistant', full_text)
            _save_session_context(session_path, session_data)

        _append_chat_history(request, promotion, full_text)
        yield helper_sse('done', {'final': full_text, 'thinking': think_text, 'audio_url': None})

    response = StreamingHttpResponse(generate(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response
