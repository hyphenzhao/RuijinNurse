import os
import re
import tempfile
from pathlib import Path

import requests
from django.test import TestCase


TEST_TEXT = '你好，世界。这是一个语音转化测试。'


def _normalize_text(value: str) -> str:
    value = (value or '').strip()
    return re.sub(r'[\s\.,!?;:，。！？；：、“”"\'（）()【】\[\]<>《》-]+', '', value)


class SpeechServiceIntegrationTests(TestCase):
    def _whisper_base_url(self):
        from django.conf import settings
        return f"http://{settings.WHISPER_HOST}:{settings.WHISPER_PORT}"

    def _piper_base_url(self):
        from django.conf import settings
        return f"http://{settings.PIPER_HOST}:{settings.PIPER_PORT}"

    def _tts_to_temp_file(self, text: str) -> Path:
        response = requests.post(
            f'{self._piper_base_url()}/tts',
            json={'text': text},
            timeout=120,
        )
        response.raise_for_status()

        tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        tmp.write(response.content)
        tmp.flush()
        tmp.close()
        return Path(tmp.name)

    def _asr_from_file(self, audio_path: Path) -> str:
        with audio_path.open('rb') as audio_file:
            response = requests.post(
                f'{self._whisper_base_url()}/asr',
                files={'file': (audio_path.name, audio_file, 'audio/wav')},
                timeout=120,
            )
        response.raise_for_status()
        payload = response.json()
        return payload.get('text', '')

    def test_can_define_whisper_and_piper_host_and_port(self):
        whisper_url = self._whisper_base_url()
        piper_url = self._piper_base_url()

        self.assertTrue(whisper_url.startswith('http://'))
        self.assertTrue(piper_url.startswith('http://'))
        self.assertIn(':', whisper_url.rsplit('//', 1)[-1])
        self.assertIn(':', piper_url.rsplit('//', 1)[-1])

    def test_can_generate_voice_and_save_to_temp_file(self):
        audio_path = self._tts_to_temp_file(TEST_TEXT)
        try:
            self.assertTrue(audio_path.exists())
            self.assertGreater(audio_path.stat().st_size, 0)
        finally:
            if audio_path.exists():
                audio_path.unlink()

    def test_generated_voice_can_roundtrip_back_to_matching_text(self):
        audio_path = self._tts_to_temp_file(TEST_TEXT)
        try:
            recognized_text = self._asr_from_file(audio_path)
            self.assertEqual(_normalize_text(recognized_text), _normalize_text(TEST_TEXT))
        finally:
            if audio_path.exists():
                audio_path.unlink()
