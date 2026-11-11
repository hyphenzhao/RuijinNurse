"""Synthetic checks for the production server and public/static boundary."""
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from django.core.exceptions import DisallowedHost
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

# Always provide an isolated config file: Windows otherwise defaults to the
# real LOCALAPPDATA deployment configuration even when the env value is empty.
with TemporaryDirectory() as config_directory:
    config_path = Path(config_directory) / 'synthetic-production.json'
    config_path.write_text('{}', encoding='utf-8')
    with patch.dict(os.environ, {
        'DJANGO_SECRET_KEY': 'synthetic-production-test-key-0123456789abcdef',
        'RUIJIN_ALLOWED_HOSTS': 'testserver,localhost,127.0.0.1',
        'RUIJIN_CONFIG_FILE': str(config_path),
    }):
        from mysite.production_settings import (
            RestrictedWhiteNoiseMiddleware, private_static_path, static_response_headers,
        )

import serve


class StaticServingTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        settings = override_settings(
            DEBUG=False, ALLOWED_HOSTS=['testserver'], STATIC_URL='/static/', STATIC_ROOT=None,
            STATICFILES_DIRS=[self.root], WHITENOISE_ROOT=None,
            STATICFILES_FINDERS=['django.contrib.staticfiles.finders.FileSystemFinder'],
            WHITENOISE_AUTOREFRESH=True, WHITENOISE_USE_FINDERS=True,
            WHITENOISE_ADD_HEADERS_FUNCTION=static_response_headers,
            WHITENOISE_MIMETYPES={'.wasm': 'application/wasm'},
        )
        settings.enable()
        self.addCleanup(settings.disable)
        self.middleware = RestrictedWhiteNoiseMiddleware(lambda request: HttpResponse(status=404))
        self.requests = RequestFactory()

    def write(self, path, content=b'synthetic content'):
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)

    def response(self, path, **headers):
        return self.middleware(self.requests.get(path, **headers))

    def test_dynamic_audio_and_static_assets_are_found_after_startup(self):
        self.write('promotions/generated/test.wav', b'0123456789')
        response = self.response('/static/promotions/generated/test.wav')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'0123456789')
        self.assertEqual(response['Cache-Control'], 'no-store')
        response.close()
        # Replacing the filename must not replay the old content.
        self.write('promotions/generated/test.wav', b'new response')
        response = self.response('/static/promotions/generated/test.wav')
        self.assertEqual(b''.join(response.streaming_content), b'new response')
        response.close()

    def test_audio_and_video_range_requests_remain_supported(self):
        self.write('promotions/intro.mp4', b'0123456789')
        response = self.response('/static/promotions/intro.mp4', HTTP_RANGE='bytes=2-5')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(b''.join(response.streaming_content), b'2345')
        response.close()

    def test_unity_assets_keep_isolation_headers_and_wasm_type(self):
        self.write('promotions/unity/Build/sample.wasm', b'synthetic wasm')
        response = self.response('/static/promotions/unity/Build/sample.wasm')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cross-Origin-Opener-Policy'], 'same-origin')
        self.assertEqual(response['Cross-Origin-Embedder-Policy'], 'require-corp')
        self.assertEqual(response['Content-Type'], 'application/wasm')
        response.close()

    def test_runtime_configuration_and_response_text_are_not_public(self):
        for relative in ('promotions/input.txt', 'promotions/single_model_config.json',
                         'promotions/msc/msc.cfg', 'promotions/runtime_sessions/user_1.json',
                         'promotions/input.txt.gz', 'promotions/.env', 'promotions/test.py'):
            self.write(relative)
            response = self.response('/static/' + relative)
            self.assertEqual(response.status_code, 404, relative)
            self.assertEqual(response['Cache-Control'], 'no-store')

    def test_encoded_windows_paths_cannot_bypass_restriction(self):
        for path in ('/static/promotions/%69nput.txt', '/static/promotions/input.txt::$DATA',
                     '/static/promotions/..%5cproduction.json', '/static/promotions/%252eenv'):
            self.assertTrue(private_static_path(path), path)
        self.assertFalse(private_static_path('/static/promotions/js/app.js'))
        self.assertFalse(private_static_path('/promotion/input.txt'))

    def test_windows_aliases_and_control_characters_are_rejected(self):
        for path in (
            '/static/promotions/input.txt.', '/static/promotions/input.txt%20',
            '/static/promotions/single_model_config.json.',
            '/static/promotions/role.txt.%20', '/static/promotions/msc%20/file.cfg',
            '/static/promotions/input.txt%00', '/static/promotions/input.txt%09',
            '/static/promotions/input.txt%7f',
            '/static/promotions/LATEST~1.TXT', '/static/promotions/SINGLE~1.JSO',
            '/static/promotions/KNOWLE%7e1.TXT',
        ):
            with self.subTest(path=path):
                self.assertTrue(private_static_path(path))
        self.assertFalse(private_static_path('/static/promotions/a normal image.png'))

    def test_host_is_checked_even_for_static_response(self):
        self.write('promotions/logo.png')
        with self.assertRaises(DisallowedHost):
            self.response('/static/promotions/logo.png', HTTP_HOST='unapproved.example')


class ProductionStartupTests(TestCase):
    def test_production_rejects_debug_before_development_key_generation(self):
        with TemporaryDirectory() as directory:
            config = Path(directory) / 'synthetic-config.json'
            config.write_text('{}', encoding='utf-8')
            env = os.environ.copy()
            env.pop('DJANGO_SECRET_KEY', None)
            env.update({'DJANGO_DEBUG': 'true', 'RUIJIN_CONFIG_FILE': str(config)})
            result = subprocess.run(
                [sys.executable, '-c', 'import mysite.production_settings'],
                cwd=Path(__file__).resolve().parent, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Production entry point refuses DJANGO_DEBUG', result.stderr)

    def test_default_server_is_threaded_single_process_with_bounded_configuration(self):
        options = serve.server_options({})
        self.assertEqual(options['threads'], 16)
        self.assertEqual(options['port'], 80)
        self.assertGreater(options['channel_timeout'], 900)
        self.assertFalse(options['expose_tracebacks'])
        for env in ({'WAITRESS_PORT': '0'}, {'WAITRESS_THREADS': '1'}):
            with self.assertRaises(ValueError):
                serve.server_options(env)

    def test_exclusive_lock_is_released_after_exit(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'server.lock'
            with serve.single_process_lock(path):
                with self.assertRaises(RuntimeError):
                    with serve.single_process_lock(path):
                        pass
            with serve.single_process_lock(path):
                pass

    def test_check_does_not_bind_or_change_timing_records(self):
        with patch.dict(os.environ), patch('django.core.wsgi.get_wsgi_application'), \
                patch('django.core.management.call_command') as command, \
                patch('waitress.create_server') as create:
            serve.main(['--check'])
            command.assert_called_once_with('check', verbosity=0)
            create.assert_not_called()

    def test_startup_reserves_port_then_recovers_before_accepting_requests(self):
        events = []
        server = Mock()
        server.run.side_effect = lambda: events.append('run')
        def create(*args, **kwargs):
            events.append('bind')
            return server
        with TemporaryDirectory() as directory, patch.dict(os.environ, {'RUIJIN_SERVER_LOCKFILE': str(Path(directory) / 'server.lock')}), \
                patch('django.core.wsgi.get_wsgi_application'), \
                patch('django.core.management.call_command', side_effect=lambda name, **kw: events.append(name)), \
                patch('waitress.create_server', side_effect=create):
            serve.main([])
        self.assertEqual(events, ['check', 'bind', 'recover_question_timings', 'run'])
        server.close.assert_called_once()

    def test_bind_failure_does_not_recover_live_requests(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {'RUIJIN_SERVER_LOCKFILE': str(Path(directory) / 'server.lock')}), \
                patch('django.core.wsgi.get_wsgi_application'), \
                patch('django.core.management.call_command') as command, \
                patch('waitress.create_server', side_effect=OSError('already bound')):
            with self.assertRaises(OSError):
                serve.main([])
        command.assert_called_once_with('check', verbosity=0)
