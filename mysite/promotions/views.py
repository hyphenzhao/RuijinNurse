from django import forms
from django.shortcuts import render, get_object_or_404
from openai import OpenAI
from django.conf import settings
import qianfan, subprocess, shutil, re, os, json, time, requests
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie, csrf_protect
from django.utils.encoding import smart_str
from .models import *
from .forms import *
from .view_helper import *
import os, json, time, requests

# 如果后面用到第三方库，需要先 pip install:
# pip install python-docx python-pptx PyPDF2

# from TTS.api import TTS

os.environ["QIANFAN_AK"] = "JzIBwbIe4DwrJI39ZgnufZ7L"
os.environ["QIANFAN_SK"] = "bb9KUEHTjMEZ6wL5Cfl1I1iVqrfifdLU"

def setup_view(request):
    base_path = '/Volumes/Workspace/RuijinNurse/mysite/promotions/static/promotions/'  # or specify another directory
    role_file_path = os.path.join(base_path, 'role.txt')
    knowledge_file_path = os.path.join(base_path, 'knowledge.txt')
    role_text, knowledge_text = load_role_and_knowledge(base_path)

    agents = Agent.objects.all().order_by('-is_active', 'name')
    agent_form = AgentForm()
    # 新增：把已上传的知识文档也传给模板
    knowledge_docs = KnowledgeDocument.objects.all().order_by('-created_at')

    upload_message = None  # 简单反馈信息

    if request.method == 'POST':
        form_type = request.POST.get('form_type', '')

        # --- 1) 老的 角色/背景 编辑表单 ---
        if form_type == 'role_knowledge':
            action = request.POST.get('action')
            if action == 'save_role':
                role_text = request.POST.get('role_text', '')
                with open(role_file_path, 'w+', encoding='utf-8') as f:
                    f.write(role_text)
            elif action == 'save_knowledge':
                knowledge_text = request.POST.get('knowledge_text', '')
                with open(knowledge_file_path, 'w+', encoding='utf-8') as f:
                    f.write(knowledge_text)
            elif action == 'load_role' and os.path.exists(role_file_path):
                with open(role_file_path, 'r', encoding='utf-8') as f:
                    role_text = f.read()
            elif action == 'load_knowledge' and os.path.exists(knowledge_file_path):
                with open(knowledge_file_path, 'r', encoding='utf-8') as f:
                    knowledge_text = f.read()

        # --- 2) 新的 Agent 创建表单 ---
        elif form_type == 'agent':
            agent_form = AgentForm(request.POST)
            if agent_form.is_valid():
                agent_form.save()
                agent_form = AgentForm()
                agents = Agent.objects.all().order_by('-is_active', 'name')

        # --- 3) Agent 启用/停用/删除 动作 ---
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

        # --- 4) 上传 Word / PPT / PDF 作为知识库 ---
        elif form_type == 'upload_knowledge_doc':
            upload_file = request.FILES.get('knowledge_file')
            title = request.POST.get('title', '') or (upload_file.name if upload_file else '未命名文件')
            if upload_file:
                try:
                    extracted_text = extract_text_from_upload(upload_file)

                    # 保存到数据库
                    KnowledgeDocument.objects.create(
                        title=title,
                        content=extracted_text
                    )

                    # 可选：附加到全局 knowledge.txt 里，让当前聊天也能立刻用到
                    knowledge_text = (knowledge_text or '') + "\n\n" + extracted_text
                    with open(knowledge_file_path, 'w+', encoding='utf-8') as f:
                        f.write(knowledge_text)

                    upload_message = f"已成功导入知识文档：{title}"
                    # 重新获取文档列表
                    knowledge_docs = KnowledgeDocument.objects.all().order_by('-created_at')
                except Exception as e:
                    upload_message = f"上传失败: {type(e).__name__}: {e}"
            else:
                upload_message = "请选择一个文件再上传。"

    return render(request, 'promotions/setup.html', {
        'role_text': role_text,
        'knowledge_text': knowledge_text,
        'agent_form': agent_form,
        'agents': agents,
        'knowledge_docs': knowledge_docs,
        'upload_message': upload_message,
    })



def promotion_view(request):
    if 'chat_history' not in request.session:
        request.session['chat_history'] = []

    # Check if the request is an AJAX request
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    chat_history = []
    if request.method == "POST" and is_ajax:
        agents = Agent.objects.filter(is_active=True)
        form = PromotionForm(request.POST, agent_choices=agents)
        if form.is_valid():
            base_path = '/Volumes/Workspace/RuijinNurse/mysite/promotions/static/promotions/'
            role_text, knowledge_text = load_role_and_knowledge(base_path)

            promotion = form.cleaned_data['promotion_text']
            model = form.cleaned_data['model_select']

            agent = None
            # 如果选择的是智能体选项，读取对应 Agent
            if model.startswith('agent:'):
                slug = model.split(':', 1)[1]
                agent = Agent.objects.get(slug=slug)
                # 用 Agent 中配置的 prompt / knowledge 覆盖默认
                if agent.system_prompt:
                    role_text = agent.system_prompt
                if agent.knowledge:
                    knowledge_text = agent.knowledge

            # ====== 本地 Ollama 模型，包括智能体和固定 deepseek/gemma ======
            if model in ('l-deepseek', 'l-gemma') or agent is not None:
                if agent is not None:
                    model_name = agent.ollama_model
                else:
                    model_dict = {'l-deepseek': "deepseek-r1:32b", 'l-gemma': "gemma3:27b"}
                    model_name = model_dict[model]

                url = "http://localhost:11434/api/chat"
                payload = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": role_text},
                        {"role": "system", "content": knowledge_text},
                        {"role": "user", "content": f"你现在的角色是{role_text}，现在回答问题：{promotion}"}
                    ]
                }
                print(f"Current role: {role_text} {knowledge_text}")
                response = requests.post(url, json=payload, stream=True)
                response_text = ""
                for line in response.iter_lines(decode_unicode=True):
                    if line:
                        try:
                            data = json.loads(line)
                            content = data.get("message", {}).get("content", "")
                            response_text += content
                            if data.get("done", True):
                                break
                        except json.JSONDecodeError:
                            print("Failed to decode JSON:", line)
                            continue
                if '</think>' in response_text:
                    response_text = response_text.split('</think>')[1]
                print(response_text)

            elif model == 'r-kimi':
                client = OpenAI(api_key="sk-cf3KlGpH7oBZX2hYYpHQ9spDWHxMWqLFeYd5t5T1r8YGwXfm",
                                base_url="https://api.moonshot.cn/v1")
                completion = client.chat.completions.create(model="moonshot-v1-8k",
                        messages=[
                            {"role": "system", "content": "你是瑞金医院功能神经外科的护士，现在负责给患者解答疑问，回答问题不要超过150个字。"},
                            {"role": "user", "content": f"你好，{promotion}"}
                        ], temperature=0.3,
                )
                response_text = completion.choices[0].message.content
            elif model == 'r-baidu':
                chat_comp = qianfan.ChatCompletion()
                resp = chat_comp.do(model="ERNIE-4.0-8K", messages=[{
                                        "role": "user",
                                        "content": f"你好，{promotion}"
                                    }])
                response_text = resp["body"]['result']
            else:
                response_text = f"{promotion} (使用模型: {model})"

            print(response_text.replace('**', ''))
            
            # Update chat history in session
            chat_history = request.session['chat_history']
            chat_history.append({'user': promotion, 'response': response_text})
            request.session['chat_history'] = chat_history

            # Save response_text to input.txt for TTS
            input_text = response_text.replace('**', '').replace('\n', '')
            input_fpath = os.path.join(working_directory, 'input.txt')
            with open(input_fpath, 'w+') as f:
                f.write(input_text)

            audio_url = None
            return JsonResponse({'chat_history': chat_history, 'audio_url': audio_url})
        else:
            return JsonResponse({'error': form.errors}, status=400)
    else:
        agents = Agent.objects.filter(is_active=True)
        form = PromotionForm(agent_choices=agents)
        if 'chat_history' in request.session:
            del request.session['chat_history']
        # Show intro video only the very first time
        show_intro = True
        # if not request.session.get('intro_shown', False):
        #     show_intro = True
        #     request.session['intro_shown'] = True

        return render(
            request,
            'promotions/promotion.html',
            {
                'form': form,
                'chat_history': chat_history,
                'show_intro': show_intro,  # <— NEW
            }
        )
    return render(request, 'promotions/promotion.html', {'form': form, 'chat_history': chat_history})



# --- NEW: Streaming endpoint ---------------------------------------
@ensure_csrf_cookie
def promotion_stream(request):
    if request.method != "POST":
        return JsonResponse({'error': 'POST only'}, status=405)

    # make sure dynamic agent choices are included, same as promotion_view
    agents = Agent.objects.filter(is_active=True)
    form = PromotionForm(request.POST, agent_choices=agents)
    if not form.is_valid():
        print("promotion_stream form errors:", form.errors)
        return JsonResponse({'error': form.errors}, status=400)

    # --- your original plumbing ---
    base_path = '/Volumes/Workspace/RuijinNurse/mysite/promotions/static/promotions/'
    role_text, knowledge_text = load_role_and_knowledge(base_path)
    promotion = form.cleaned_data['promotion_text']
    model = form.cleaned_data['model_select']
    agent = None
    if model.startswith('agent:'):
        slug = model.split(':', 1)[1]
        agent = Agent.objects.get(slug=slug)
        if agent.system_prompt:
            role_text = agent.system_prompt
        if agent.knowledge:
            knowledge_text = agent.knowledge

    def generate():
        # tell UI to show grey "thinking…" with jumping dots
        yield helper_sse('status', {'message': 'thinking'})

        full_text = ""
        think_state = {'in_think': False}

        try:
            if model in ('l-deepseek', 'l-gemma') or agent is not None:
                if agent is not None:
                    model_name = agent.ollama_model
                else:
                    model_dict = {'l-deepseek': "deepseek-r1:32b", 'l-gemma': "gemma3:27b"}
                    model_name = model_dict[model]

                url = "http://localhost:11434/api/chat"
                payload = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": role_text},
                        {"role": "system", "content": knowledge_text},
                        {"role": "user", "content": f"你现在的角色是{role_text}，现在回答问题：{promotion}"}
                    ]
                }
                with requests.post(url, json=payload, stream=True) as r:
                    r.raise_for_status()
                    for line in r.iter_lines(decode_unicode=True):
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        delta = data.get("message", {}).get("content", "")
                        safe_delta = helper_filter_think_stream(delta, think_state)
                        if safe_delta:
                            full_text += safe_delta
                            yield helper_sse('delta', {'content': safe_delta})
                        if data.get("done", True):
                            break

            elif model == 'r-kimi':
                full_text = "（示例）您好，我来为您解答该问题的要点……"
                for chunk in helper_chunk_iter(full_text):
                    yield helper_sse('delta', {'content': chunk})

            elif model == 'r-baidu':
                # chat_comp = qianfan.ChatCompletion()
                # resp = chat_comp.do(model="ERNIE-4.0-8K", messages=[{"role": "user","content": f"你好，{promotion}"}])
                # full_text = resp["body"]['result']
                full_text = "（示例）已为您查询到以下与问题相关的信息……"
                for chunk in helper_chunk_iter(full_text):
                    yield helper_sse('delta', {'content': chunk})

            else:
                full_text = f"{promotion} (使用模型: {model})"
                for chunk in helper_chunk_iter(full_text):
                    yield helper_sse('delta', {'content': chunk})

            # final cleanup if any residual tags slipped (defensive)
            if '</think>' in full_text:
                full_text = full_text.split('</think>')[-1]

        except Exception as e:
            # Surface an error message inline (keeps UI responsive)
            err = f"（服务端错误）{type(e).__name__}: {e}"
            yield helper_sse('delta', {'content': err})
            full_text = (full_text or "") + "\n" + err

        # --- persist to session history at the end ---
        chat_history = request.session.get('chat_history', [])
        chat_history.append({'user': promotion, 'response': full_text})
        request.session['chat_history'] = chat_history

        # --- you can add TTS later; keep None for now ---
        yield helper_sse('done', {'final': full_text, 'audio_url': None})

    resp = StreamingHttpResponse(generate(), content_type='text/event-stream')
    resp['Cache-Control'] = 'no-cache'
    resp['X-Accel-Buffering'] = 'no'  # helpful if behind nginx
    return resp