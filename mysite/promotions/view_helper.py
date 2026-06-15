from pathlib import Path
from datetime import datetime

from django import forms
from django.shortcuts import render
from django.conf import settings
import subprocess, shutil, re, os, json, time, requests
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie, csrf_protect
from django.utils.encoding import smart_str

# Optional imports — only needed by specific functions (not used by the
# shared SSE generator or the mobile API).
try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

try:
    import qianfan
except ImportError:  # pragma: no cover
    qianfan = None


def _safe_model_key(model_key: str) -> str:
    """Sanitize model key for filesystem use."""
    return re.sub(r'[^A-Za-z0-9._-]+', '_', model_key or 'default')


def _resolve_session_identifier(request_or_user_id):
    """
    Return a stable session identifier for session-context persistence.

    If given a Django request with an authenticated user, returns 'user_{id}'.
    If given a raw user ID (int/str), returns 'user_{id}'.
    Otherwise falls back to the Django session key.
    """
    # If passed a request object with an authenticated user
    if hasattr(request_or_user_id, 'user') and request_or_user_id.user and request_or_user_id.user.is_authenticated:
        return f'user_{request_or_user_id.user.id}'

    # If passed a request object without auth, fall back to session key
    if hasattr(request_or_user_id, 'session'):
        session_key = request_or_user_id.session.session_key
        if not session_key:
            request_or_user_id.session.save()
            session_key = request_or_user_id.session.session_key
        return session_key

    # If passed a raw int or digit string (user ID)
    uid = str(request_or_user_id)
    return f'user_{uid}'


def generate_sse_stream(
    *,
    chat_target: dict,
    default_role_text: str,
    default_knowledge_text: str,
    promotion: str,
    model_key: str,
    session_path: Path,
    chat_history_callback=None,
):
    """
    Shared SSE stream generator used by both the web view and mobile API.

    Yields SSE event strings.  On completion, saves the session context
    to *session_path* and calls *chat_history_callback(model, promotion, full_text)* if given.
    """
    helper_sse = _helper_sse

    full_text = ''
    think_text = ''
    think_state = {'in_think': False}

    try:
        role_text = chat_target['role_text'] if chat_target['role_text'] is not None else default_role_text
        knowledge_text = chat_target['knowledge_text'] if chat_target['knowledge_text'] is not None else default_knowledge_text
        system_context = _build_system_context(role_text, knowledge_text)

        session_data = _load_session_context_from_path(session_path, system_context, model_key)
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

                # Final answer text
                delta = message.get('content', '')

                # Backward compatibility: some models emit <think>...</think> tags
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
                    full_text += delta
                    yield helper_sse('delta', {'content': delta})

                if data.get('done', True):
                    break

        # Save session context
        _append_session_message(session_data, 'user', promotion)
        _append_session_message(session_data, 'assistant', full_text)
        _save_session_context(session_path, session_data)

    except Exception as e:
        base_url = chat_target.get('base_url', 'unknown')
        err = f'（服务端错误）无法连接 Ollama ({base_url}): {type(e).__name__}: {e}'
        yield helper_sse('delta', {'content': err})
        full_text = (full_text or '') + '\n' + err

    if chat_history_callback:
        try:
            chat_history_callback(model_key, promotion, full_text)
        except Exception:
            pass

    yield helper_sse('done', {'final': full_text, 'thinking': think_text, 'audio_url': None})


# ---- Internal helpers used by generate_sse_stream (namespace-imported above) ----

def _helper_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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


def _build_ollama_base_url(host: str, port) -> str:
    return f'http://{str(host).strip()}:{str(port).strip()}'


def _stream_ollama_response_by_target(base_url: str, model_name: str, messages):
    import requests as _requests
    payload = {
        'model': model_name,
        'messages': messages,
        'think': True,
        'stream': True,
    }
    return _requests.post(f'{base_url}/api/chat', json=payload, stream=True)


def _split_thinking_delta(delta: str, state: dict):
    """Split <think>...</think> from answer text in one chunk."""
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


def _load_session_context_from_path(path: Path, system_context: str, model_key: str):
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

command = "./tts_offline_sample"
working_directory = "/Volumes/Workspace/RuijinNurse/mysite/promotions/static/promotions/"
STATIC_WAV_PATH = '/Volumes/Workspace/RuijinNurse/mysite/promotions/static/promotions/tts_sample.wav'
# STATIC_PATH     = '/Volumes/HDWD16TB/Workspace/RuijinNurse/mysite/promotions/static/promotions/'


def load_role_and_knowledge(base_path):
    role_file_path = os.path.join(base_path, 'role.txt')
    knowledge_file_path = os.path.join(base_path, 'knowledge.txt')

    # Initialize variables in case files don't exist
    role_text = ''
    knowledge_text = ''
    if os.path.exists(role_file_path):
        with open(role_file_path, 'r', encoding='utf-8') as f:
            role_text = f.read()
    if os.path.exists(knowledge_file_path):
        with open(knowledge_file_path, 'r', encoding='utf-8') as f:
            knowledge_text = f.read()
    return role_text, knowledge_text

# def run_tts_program():
#     try:
#         result = subprocess.run(
#             command, 
#             cwd=working_directory,  # Set the working directory
#             check=True,  # Raises an exception if the command fails
#             stdout=subprocess.PIPE,  # Capture standard output
#             stderr=subprocess.PIPE   # Capture standard error
#         )

#         # Print the output of the command
#         print("Output:", result.stdout.decode())
#         print("Error:", result.stderr.decode())

#     except subprocess.CalledProcessError as e:
#         print("An error occurred while running the command.")
#         print("Error code:", e.returncode)
#         print("Error message:", e.stderr.decode())
def split_chinese_sentences(text):
    # The regex pattern splits on Chinese punctuation that is used for ending sentences.
    # It uses a capturing group so that the punctuation is preserved.
    parts = re.split(r'([。！？：；])', text)
    
    sentences = []
    sentence = ""
    # Reassemble the text parts and punctuation into full sentences.
    for part in parts:
        # Append the part (could be text or punctuation) to the current sentence.
        sentence += part.strip()
        # If the part is a Chinese punctuation mark that ends a sentence,
        # we consider the sentence complete.
        if re.fullmatch(r'[。！？]', part):
            sentences.append(sentence)
            sentence = ""
    # If there's any remaining text without a trailing punctuation, add it as well.
    if sentence:
        sentences.append(sentence)
    return sentences

def run_tts_program(generated_audio, full_content):
    # Define the OpenTTS URL; note that voice can be passed either in the URL or in the payload.
    opentts_url = "http://localhost:5500/api/tts?voice=coqui-tts:zh_baker"
    # opentts_url = "http://localhost:5500/api/tts?voice=espeak:zh"

    # Build your payload with the Chinese text.
    payload = {
        "text": split_chinese_sentences(full_content)  # e.g., "你好"
    }
    
    # Convert payload to a JSON string using ensure_ascii=False so that non-ASCII characters remain intact.
    payload_json = json.dumps(full_content, ensure_ascii=False)
    
    # Set the header to indicate UTF-8 encoding.
    headers = {"Content-Type": "application/json; charset=utf-8"}

    # Send the POST request using data=payload_json to use your custom JSON encoding.
    response = requests.post(opentts_url, data=full_content.replace("；", "。").replace("：", "。"), headers=headers)
    
    # Debug: print the response content for verification
    # print("Response content:", response.content)
    
    # Check the response status code.
    if response.status_code == 200:
        # Optionally check that the returned content type is audio/wav.
        content_type = response.headers.get("Content-Type", "")
        if "audio/wav" in content_type:
            with open(generated_audio, "wb") as audio_file:
                audio_file.write(response.content)
            print(f"Audio saved successfully to: {generated_audio}")
        else:
            print("Unexpected Content-Type:", content_type)
    else:
        print(f"Failed to generate audio. Status code: {response.status_code}\nResponse: {response.text}")

def convert_wav_to_mp3(input_path, output_path):
    # Run ffmpeg to convert wav to mp3
    command = ["ffmpeg", "-i", input_path, "-q:a", "2", output_path]
    try:
        subprocess.run(command, check=True)
        print("Conversion to MP3 successful.")
    except subprocess.CalledProcessError as e:
        print("An error occurred during conversion:", e)

def helper_sse(event: str, data: dict) -> str:
    """Pack an SSE-like event line."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

def helper_chunk_iter(text: str, size: int = 24):
    """Yield small chunks to simulate/standardize streaming for non-streaming APIs."""
    for i in range(0, len(text), size):
        yield text[i:i+size]

def helper_filter_think_stream(delta: str, state: dict) -> str:
    """
    Remove any <think>...</think> segments on-the-fly, maintaining state across chunks.
    state = {'in_think': bool}
    """
    if not delta:
        return ""

    out = []
    buf = delta

    while buf:
        if state['in_think']:
            end = buf.find("</think>")
            if end == -1:
                # still inside think, drop everything
                return ""
            # close tag found; drop it and switch off
            buf = buf[end+len("</think>"):]
            state['in_think'] = False
            continue

        # not currently in think; look for start tag
        start = buf.find("<think>")
        if start == -1:
            # no think tag, all is valid content
            out.append(buf)
            break
        # append content before <think>
        if start > 0:
            out.append(buf[:start])
        # enter think
        buf = buf[start+len("<think>"):]
        state['in_think'] = True
        # loop to try to find </think> in remaining buf

    return "".join(out)

def extract_text_from_upload(uploaded_file):
    """
    根据文件扩展名，从 Word / PPTX / PDF 中抽取纯文本。
    仅支持: .docx, .pptx, .pdf
    """
    import os
    ext = os.path.splitext(uploaded_file.name)[1].lower()

    if ext == '.docx':
        from docx import Document
        doc = Document(uploaded_file)
        return "\n".join(p.text for p in doc.paragraphs)

    elif ext == '.pptx':
        from pptx import Presentation
        prs = Presentation(uploaded_file)
        texts = []
        for slide in prs.slides:
            for shape in slide.shapes:
                if hasattr(shape, "text"):
                    texts.append(shape.text)
        return "\n".join(texts)

    elif ext == '.pdf':
        from PyPDF2 import PdfReader
        reader = PdfReader(uploaded_file)
        texts = []
        for page in reader.pages:
            txt = page.extract_text() or ""
            texts.append(txt)
        return "\n".join(texts)

    else:
        raise ValueError(f"不支持的文件类型: {ext}")
