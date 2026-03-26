from pathlib import Path
import json

import requests
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import ensure_csrf_cookie

from .forms import AgentForm, PromotionForm
from .models import Agent, KnowledgeDocument
from .view_helper import (
    extract_text_from_upload,
    helper_sse,
    load_role_and_knowledge,
)


PROMOTIONS_STATIC_DIR = Path(__file__).resolve().parent / 'static' / 'promotions'
SINGLE_MODEL_CONFIG_PATH = PROMOTIONS_STATIC_DIR / 'single_model_config.json'
OLLAMA_CHAT_URL = 'http://localhost:11434/api/chat'
LOCAL_MODEL_MAP = {
    'l-deepseek': 'deepseek-r1:32b',
    'l-gemma': 'gemma3:27b',
    'l-other': 'gemma3:27b',
}
OLLAMA_TIMEOUT_SECONDS = 8


def _get_base_path() -> str:
    return str(PROMOTIONS_STATIC_DIR)


def _build_messages(role_text: str, knowledge_text: str, promotion: str):
    return [
        {'role': 'system', 'content': role_text},
        {'role': 'system', 'content': knowledge_text},
        {'role': 'user', 'content': f'你现在的角色是{role_text}，现在回答问题：{promotion}'},
    ]


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


def _stream_ollama_response_by_target(base_url: str, model_name: str, role_text: str, knowledge_text: str, promotion: str):
    payload = {
        'model': model_name,
        'messages': _build_messages(role_text, knowledge_text, promotion),
        'think': True,
        'stream': True,
    }
    return requests.post(f'{base_url}/api/chat', json=payload, stream=True)


def _stream_ollama_response(model_key: str, role_text: str, knowledge_text: str, promotion: str):
    return _stream_ollama_response_by_target(
        _build_ollama_base_url('127.0.0.1', '11434'),
        _resolve_local_model(model_key),
        role_text,
        knowledge_text,
        promotion,
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


def setup_view(request):
    base_path = _get_base_path()
    role_file_path = Path(base_path) / 'role.txt'
    knowledge_file_path = Path(base_path) / 'knowledge.txt'
    role_text, knowledge_text = load_role_and_knowledge(base_path)

    agents = Agent.objects.all().order_by('-is_active', 'name')
    agent_form = AgentForm()
    knowledge_docs = KnowledgeDocument.objects.all().order_by('-created_at')
    single_model_config = _load_single_model_config()
    upload_message = None

    if request.method == 'POST':
        form_type = request.POST.get('form_type', '')

        if form_type == 'role_knowledge':
            action = request.POST.get('action')
            if action == 'save_role':
                role_text = request.POST.get('role_text', '')
                role_file_path.write_text(role_text, encoding='utf-8')
            elif action == 'save_knowledge':
                knowledge_text = request.POST.get('knowledge_text', '')
                knowledge_file_path.write_text(knowledge_text, encoding='utf-8')
            elif action == 'load_role' and role_file_path.exists():
                role_text = role_file_path.read_text(encoding='utf-8')
            elif action == 'load_knowledge' and knowledge_file_path.exists():
                knowledge_text = knowledge_file_path.read_text(encoding='utf-8')

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
                        agent_form.save()
                        agent_form = AgentForm()
                        agents = Agent.objects.all().order_by('-is_active', 'name')
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
                        agents = Agent.objects.all().order_by('-is_active', 'name')
                except Exception as e:
                    upload_message = f'智能体更新失败: 无法连接 Ollama 服务（{type(e).__name__}: {e}）'
            else:
                upload_message = f'智能体更新失败: {edit_form.errors.as_text()}'

        elif form_type == 'agent_action':
            agent_id = request.POST.get('agent_id')
            action = request.POST.get('action')
            agent = get_object_or_404(Agent, id=agent_id)
            if action == 'toggle_active':
                agent.is_active = not agent.is_active
                agent.save()
            elif action == 'delete':
                agent.delete()
            agents = Agent.objects.all().order_by('-is_active', 'name')

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

        elif form_type == 'upload_knowledge_doc':
            upload_file = request.FILES.get('knowledge_file')
            title = request.POST.get('title', '') or (upload_file.name if upload_file else '未命名文件')
            if upload_file:
                try:
                    extracted_text = extract_text_from_upload(upload_file)
                    KnowledgeDocument.objects.create(title=title, content=extracted_text)
                    knowledge_text = (knowledge_text or '') + '\n\n' + extracted_text
                    knowledge_file_path.write_text(knowledge_text, encoding='utf-8')
                    upload_message = f'已成功导入知识文档：{title}'
                    knowledge_docs = KnowledgeDocument.objects.all().order_by('-created_at')
                except Exception as e:
                    upload_message = f'上传失败: {type(e).__name__}: {e}'
            else:
                upload_message = '请选择一个文件再上传。'

    return render(request, 'promotions/setup.html', {
        'role_text': role_text,
        'knowledge_text': knowledge_text,
        'agent_form': agent_form,
        'agents': agents,
        'knowledge_docs': knowledge_docs,
        'single_model_config': single_model_config,
        'upload_message': upload_message,
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
            response_text = ''
            with _stream_ollama_response_by_target(
                chat_target['base_url'],
                chat_target['model_name'],
                role_text,
                knowledge_text,
                promotion,
            ) as response:
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
        except Exception as e:
            response_text = f'（服务端错误）{type(e).__name__}: {e}'

        chat_history = _append_chat_history(request, promotion, response_text)
        _save_latest_response_text(response_text)

        return JsonResponse({'chat_history': chat_history, 'audio_url': None})

    agents = Agent.objects.filter(is_active=True).order_by('name')
    model_choices = _get_runtime_model_choices()
    form = PromotionForm(agent_choices=agents, model_choices=model_choices)
    if 'chat_history' in request.session:
        del request.session['chat_history']

    return render(request, 'promotions/promotion.html', {
        'form': form,
        'chat_history': chat_history,
        'show_intro': True,
        'agent_options': [(f'agent:{agent.slug}', f'智能体（{agent.name}）') for agent in agents],
        'model_options': model_choices,
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

        try:
            chat_target = _get_chat_target(model)
            role_text = chat_target['role_text'] if chat_target['role_text'] is not None else default_role_text
            knowledge_text = chat_target['knowledge_text'] if chat_target['knowledge_text'] is not None else default_knowledge_text

            with _stream_ollama_response_by_target(
                chat_target['base_url'],
                chat_target['model_name'],
                role_text,
                knowledge_text,
                promotion,
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

        _append_chat_history(request, promotion, full_text)
        yield helper_sse('done', {'final': full_text, 'thinking': think_text, 'audio_url': None})

    response = StreamingHttpResponse(generate(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response
