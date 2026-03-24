from django import forms
from django.shortcuts import render
from openai import OpenAI
from django.conf import settings
import qianfan, subprocess, shutil, re, os, json, time, requests
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie, csrf_protect
from django.utils.encoding import smart_str
from .models import *
from .forms import *

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
