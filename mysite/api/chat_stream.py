"""Mobile streaming with the same outcome and timing semantics as the web UI."""
import json
import logging

import requests

from promotions.qa_store import log_qa_record
from promotions.qa_timing import failure_status, ollama_request_timeout
from promotions.view_helper import (
    _append_session_message, _build_messages, _build_system_context,
    _history_messages_for_ollama, _load_session_context_from_path,
    _save_session_context, _split_thinking_delta, helper_sse,
)


logger = logging.getLogger(__name__)

def generate_chat_stream(*, request, chat_target, default_role_text,
                         default_knowledge_text, promotion, model_key,
                         session_path, patient, timer):
    """Preserve the mobile SSE fields; add request identity and explicit outcome."""
    full_text = ''
    think_text = ''
    think_state = {'in_think': False}
    result, error_text = 'interrupted', '模型连接提前结束'
    session_data = None
    timing_id = str(timer.timing_id) if timer.timing_id else None
    try:
        yield helper_sse('status', {'message': 'thinking', 'timing_id': timing_id})
        role = chat_target['role_text']
        knowledge = chat_target['knowledge_text']
        system_context = _build_system_context(
            default_role_text if role is None else role,
            default_knowledge_text if knowledge is None else knowledge,
        )
        session_data = _load_session_context_from_path(session_path, system_context, model_key)
        session_data['system_context'] = system_context
        session_data['patient_id'] = patient.pk if patient else None
        payload = {
            'model': chat_target['model_name'], 'think': True, 'stream': True,
            'messages': _build_messages(system_context, _history_messages_for_ollama(session_data), promotion),
        }
        completed = False
        if not timer.cancelled:
            response = requests.post(
                f"{chat_target['base_url']}/api/chat", json=payload, stream=True,
                timeout=ollama_request_timeout(),
            )
            with timer.track_response(response):
                response.raise_for_status()
                for line in response.iter_lines(decode_unicode=True):
                    if timer.cancelled:
                        break
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if data.get('error'):
                        raise RuntimeError('模型服务返回错误')
                    message = data.get('message') or {}
                    direct_thinking = message.get('thinking') or ''
                    if direct_thinking:
                        think_text += direct_thinking
                        yield helper_sse('thinking', {'content': direct_thinking})
                    thinking, answer = _split_thinking_delta(message.get('content') or '', think_state)
                    if thinking:
                        think_text += thinking
                        yield helper_sse('thinking', {'content': thinking})
                    if answer:
                        timer.first_answer(answer)
                        full_text += answer
                        yield helper_sse('delta', {'content': answer})
                    if data.get('done') is True:
                        completed = True
                        break
        if timer.cancelled:
            result, error_text = 'cancelled', '用户已取消'
        elif completed and full_text.strip():
            result, error_text = 'success', ''
        elif completed:
            result, error_text = 'failed', '模型未返回答案'
    except GeneratorExit:
        result, error_text = ('cancelled', '用户已取消') if timer.cancelled else ('interrupted', '客户端连接已断开')
        raise
    except Exception as exc:
        result = 'cancelled' if timer.cancelled else failure_status(exc)
        error_text = '用户已取消' if timer.cancelled else ('模型响应超时' if result == 'timeout' else '模型服务暂时不可用')
    finally:
        if timer.cancelled:
            result, error_text = 'cancelled', '用户已取消'
        # Failed or truncated answers remain available for analysis, never as
        # factual assistant context for a subsequent clinical question.
        if result == 'success' and session_data is not None:
            _append_session_message(session_data, 'user', promotion)
            _append_session_message(session_data, 'assistant', full_text)
            try:
                _save_session_context(session_path, session_data)
            except OSError:
                logger.warning('Could not persist API chat context')
        record = log_qa_record(
            user=request.user, source='api', patient=patient,
            model_key=model_key, question=promotion, answer=full_text,
            thinking=think_text, latency_ms=timer.elapsed_ms(),
            session_scope=f'user_{request.user.pk}', error=error_text,
        )
        timer.finish(result, record)

    if result != 'success':
        yield helper_sse('error', {'message': error_text, 'result': result})
    yield helper_sse('done', {
        'final': full_text if result == 'success' else f'（回答未完成：{error_text}）\n\n{full_text}',
        'thinking': think_text, 'audio_url': None,
        'result': result, 'error': error_text or None, 'timing_id': timing_id,
        'backend_ms': timer.elapsed_ms(), 'first_answer_ms': timer.first_answer_ms,
    })
