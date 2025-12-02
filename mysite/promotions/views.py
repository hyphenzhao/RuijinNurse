from django import forms
from django.shortcuts import render
from openai import OpenAI
from django.conf import settings
import qianfan, subprocess, shutil, re, os, json, time, requests
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie, csrf_protect
from django.utils.encoding import smart_str
# from TTS.api import TTS

os.environ["QIANFAN_AK"] = "JzIBwbIe4DwrJI39ZgnufZ7L"
os.environ["QIANFAN_SK"] = "bb9KUEHTjMEZ6wL5Cfl1I1iVqrfifdLU"
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

def setup_view(request):
    # Determine file paths (assuming you want to keep them in your BASE_DIR or a specific folder)
    base_path = '/Volumes/Workspace/RuijinNurse/mysite/promotions/static/promotions/'  # or specify another directory
    role_file_path = os.path.join(base_path, 'role.txt')
    knowledge_file_path = os.path.join(base_path, 'knowledge.txt')
    role_text, knowledge_text = load_role_and_knowledge(base_path)
    # Handle POST actions: load or save based on button clicked
    if request.method == 'POST':
        action = request.POST.get('action')
        # Save actions
        if action == 'save_role':
            role_text = request.POST.get('role_text', '')
            with open(role_file_path, 'w+', encoding='utf-8') as f:
                f.write(role_text)
        elif action == 'save_knowledge':
            knowledge_text = request.POST.get('knowledge_text', '')
            with open(knowledge_file_path, 'w+', encoding='utf-8') as f:
                f.write(knowledge_text)
        # Load actions: simply reload from file if available
        elif action == 'load_role' and os.path.exists(role_file_path):
            with open(role_file_path, 'r', encoding='utf-8') as f:
                role_text = f.read()
        elif action == 'load_knowledge' and os.path.exists(knowledge_file_path):
            with open(knowledge_file_path, 'r', encoding='utf-8') as f:
                knowledge_text = f.read()

    # Render the template with the loaded or saved content
    return render(request, 'promotions/setup.html', {
        'role_text': role_text,
        'knowledge_text': knowledge_text,
    })

# Form class for promotion submission
class PromotionForm(forms.Form):
    promotion_text = forms.CharField(widget=forms.Textarea, label="Your Promotion")
    model_select = forms.ChoiceField(
        choices=[
            ('l-deepseek', '本地-深度求索'),
            ('l-gemma', '本地-Gemma'),
            ('r-kimi', '云端-Kimi'),
            ('r-baidu', '云端-文心一言'),
            ('l-ziya', '本地-姜子牙'),
            ('l-other', '本地-其他'),
        ],
        label="选择模型"
    )

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

def promotion_view(request):
    if 'chat_history' not in request.session:
        request.session['chat_history'] = []

    # Check if the request is an AJAX request
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    chat_history = []
    if request.method == "POST" and is_ajax:
        form = PromotionForm(request.POST)
        if form.is_valid():
            base_path = '/Volumes/Workspace/RuijinNurse/mysite/promotions/static/promotions/'  # or specify another directory
            role_text, knowledge_text = load_role_and_knowledge(base_path)
            promotion = form.cleaned_data['promotion_text']
            model = form.cleaned_data['model_select']
            if model == 'l-deepseek' or model == 'l-gemma':
                # Define the URL for the API endpoint
                model_dict = {'l-deepseek': "deepseek-r1:32b", 'l-gemma': "gemma3:27b"}
                model_name = model_dict[model]
                url = "http://localhost:11434/api/chat"
                payload = {
                    "model": model_name,
                    "messages": [
                    # "你是瑞金医院功能神经外科的护士，现在负责给患者解答疑问，回答问题不要超过150个字。"
                        {"role": "system", "content": role_text},
                        {"role": "system", "content": knowledge_text},
                        {"role": "user", "content": f"你现在的角色是{role_text}，现在回答问题：{promotion}"}
                    ]
                }
                print(f"Current role: {role_text} {knowledge_text}")
                # Make a POST request using the 'json' parameter which automatically sets the 'Content-Type: application/json' header
                response = requests.post(url, json=payload, stream=True)
                response_text = ""
                for line in response.iter_lines(decode_unicode=True):
                    if line:  # Make sure the line is not empty
                        try:
                            # Convert the JSON string into a Python dict
                            data = json.loads(line)
                            # Extract the assistant's message content
                            content = data.get("message", {}).get("content", "")
                            response_text += content
                            # Check if this line signals the end of the stream
                            if data.get("done", True): break
                        except json.JSONDecodeError:
                            # Optionally handle decoding errors here
                            print("Failed to decode JSON:", line)
                            continue
                if '</think>' in response_text: response_text = response_text.split('</think>')[1]
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


            # generated_audio = os.path.join(working_directory, 'tts_sample.wav')
            # # Run the TTS program
            # run_tts_program(generated_audio, input_text)
            # # convert_wav_to_mp3(generated_audio, STATIC_WAV_PATH)
            # try:
            #     shutil.copyfile(generated_audio, STATIC_WAV_PATH)
            #     print("Audio file copied to static folder.")
            # except:
            #     pass

            # # Return response along with the path to the audio file in static
            # audio_url = '/static/promotions/tts_sample.wav'
            audio_url = None
            return JsonResponse({'chat_history': chat_history, 'audio_url': audio_url})
        else:
            return JsonResponse({'error': form.errors}, status=400)
    else:
        form = PromotionForm()
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

def _sse(event: str, data: dict) -> str:
    """Pack an SSE-like event line."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

def _chunk_iter(text: str, size: int = 24):
    """Yield small chunks to simulate/standardize streaming for non-streaming APIs."""
    for i in range(0, len(text), size):
        yield text[i:i+size]

def _filter_think_stream(delta: str, state: dict) -> str:
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

# --- NEW: Streaming endpoint ---------------------------------------
@ensure_csrf_cookie
def promotion_stream(request):
    if request.method != "POST":
        return JsonResponse({'error': 'POST only'}, status=405)

    form = PromotionForm(request.POST)
    if not form.is_valid():
        return JsonResponse({'error': form.errors}, status=400)

    # --- your original plumbing ---
    base_path = '/Volumes/Workspace/RuijinNurse/mysite/promotions/static/promotions/'
    role_text, knowledge_text = load_role_and_knowledge(base_path)
    promotion = form.cleaned_data['promotion_text']
    model = form.cleaned_data['model_select']

    def generate():
        # tell UI to show grey "thinking…" with jumping dots
        yield _sse('status', {'message': 'thinking'})

        full_text = ""
        think_state = {'in_think': False}

        try:
            if model in ('l-deepseek', 'l-gemma'):
                # --- Stream from local model (Ollama-like) ---
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
                        # drop chain-of-thought, keep answer only
                        safe_delta = _filter_think_stream(delta, think_state)
                        if safe_delta:
                            full_text += safe_delta
                            yield _sse('delta', {'content': safe_delta})
                        if data.get("done", True):
                            break

            elif model == 'r-kimi':
                full_text = "（示例）您好，我来为您解答该问题的要点……"
                for chunk in _chunk_iter(full_text):
                    yield _sse('delta', {'content': chunk})

            elif model == 'r-baidu':
                # chat_comp = qianfan.ChatCompletion()
                # resp = chat_comp.do(model="ERNIE-4.0-8K", messages=[{"role": "user","content": f"你好，{promotion}"}])
                # full_text = resp["body"]['result']
                full_text = "（示例）已为您查询到以下与问题相关的信息……"
                for chunk in _chunk_iter(full_text):
                    yield _sse('delta', {'content': chunk})

            else:
                full_text = f"{promotion} (使用模型: {model})"
                for chunk in _chunk_iter(full_text):
                    yield _sse('delta', {'content': chunk})

            # final cleanup if any residual tags slipped (defensive)
            if '</think>' in full_text:
                full_text = full_text.split('</think>')[-1]

        except Exception as e:
            # Surface an error message inline (keeps UI responsive)
            err = f"（服务端错误）{type(e).__name__}: {e}"
            yield _sse('delta', {'content': err})
            full_text = (full_text or "") + "\n" + err

        # --- persist to session history at the end ---
        chat_history = request.session.get('chat_history', [])
        chat_history.append({'user': promotion, 'response': full_text})
        request.session['chat_history'] = chat_history

        # --- you can add TTS later; keep None for now ---
        yield _sse('done', {'final': full_text, 'audio_url': None})

    resp = StreamingHttpResponse(generate(), content_type='text/event-stream')
    resp['Cache-Control'] = 'no-cache'
    resp['X-Accel-Buffering'] = 'no'  # helpful if behind nginx
    return resp