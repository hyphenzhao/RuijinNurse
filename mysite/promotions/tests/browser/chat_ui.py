"""Real-browser regression test using only disposable data and a local mock model.

Run with Django dependencies installed, Node.js + Playwright available:
  python promotions/tests/browser/chat_ui.py
  python promotions/tests/browser/chat_ui.py --production
Optional environment variables: NODE_BINARY, PLAYWRIGHT_MODULE, CHROMIUM_EXECUTABLE.
The test never loads deployment settings or connects to a real model.
"""
import argparse
import ast
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
import os
from pathlib import Path
import socket
from socketserver import ThreadingMixIn
import subprocess
import sys
import tempfile
from threading import Thread
from types import ModuleType
import time
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--production', action='store_true', help='Exercise real Waitress and production static middleware with synthetic settings')
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(source))
    from django.conf import settings
    with tempfile.TemporaryDirectory(prefix='ruijin-chat-ui-') as tmp:
        fixture = Path(tmp)
        configuration = dict(
            BASE_DIR=source, SECRET_KEY='synthetic-browser-test-only-never-used-for-deployment', DEBUG=True,
            ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'],
            ROOT_URLCONF='mysite.urls', LOGIN_URL='/accounts/login/',
            LOGIN_EXEMPT_URLS=[r'^api/v1/', r'^static/'],
            INSTALLED_APPS=[
                'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
                'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
                'rest_framework', 'promotions',
            ],
            MIDDLEWARE=[
                'django.contrib.sessions.middleware.SessionMiddleware',
                'django.middleware.common.CommonMiddleware',
                'django.middleware.csrf.CsrfViewMiddleware',
                'django.contrib.auth.middleware.AuthenticationMiddleware',
                'django.contrib.messages.middleware.MessageMiddleware',
                'promotions.middleware.LoginRequiredMiddleware',
            ],
            TEMPLATES=[{
                'BACKEND': 'django.template.backends.django.DjangoTemplates',
                'APP_DIRS': True, 'DIRS': [],
                'OPTIONS': {'context_processors': [
                    'django.template.context_processors.request',
                    'django.contrib.auth.context_processors.auth',
                    'django.contrib.messages.context_processors.messages',
                ]},
            }],
            DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': fixture / 'db.sqlite3'}},
            REST_FRAMEWORK={'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework_simplejwt.authentication.JWTAuthentication']},
            PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
            STATIC_URL='/static/', DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
            TIME_ZONE='Asia/Shanghai', USE_TZ=True,
        )
        if args.production:
            # Import the actual production middleware without executing settings.py
            # or reading a private deployment config. The substitute module exists
            # only in this disposable test process.
            base_tree = ast.parse((source / 'mysite' / 'settings.py').read_text())
            middleware_node = next(node.value for node in base_tree.body
                if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == 'MIDDLEWARE'
                    for target in node.targets))
            configuration['MIDDLEWARE'] = ast.literal_eval(middleware_node)
            extra_static = fixture / 'static'
            unity = extra_static / 'promotions' / 'unity'
            unity.mkdir(parents=True)
            (unity / 'fixture.js').write_text('/* synthetic Unity asset */')
            (extra_static / 'promotions' / 'input.txt').write_text('synthetic private input')
            (extra_static / 'promotions' / 'fixture.wav').write_bytes(b'RIFF synthetic audio')
            configuration['STATICFILES_DIRS'] = [extra_static]
            base_module = ModuleType('mysite.settings')
            vars(base_module).update(configuration)
            sys.modules['mysite.settings'] = base_module
            production = importlib.import_module('mysite.production_settings')
            configuration.update({name: value for name, value in vars(production).items() if name.isupper()})
            assert configuration['DEBUG'] is False
            assert 'mysite.production_settings.RestrictedWhiteNoiseMiddleware' in configuration['MIDDLEWARE']
        settings.configure(**configuration)
        import django
        django.setup()
        from django.contrib.auth import get_user_model
        from django.contrib.staticfiles.handlers import StaticFilesHandler
        from django.core.management import call_command
        from django.core.wsgi import get_wsgi_application
        from django.test import Client
        from promotions import views
        from promotions.models import Patient, QATiming, QARecord

        class OllamaMock(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def handle(self):
                try:
                    super().handle()
                except ConnectionResetError:
                    pass

            def do_GET(self):
                if self.path == '/test/cancel-state':
                    payload = self.server.cancel_state
                elif self.path == '/api/tags':
                    payload = {'models': [{'name': 'fixture-ollama'}]}
                else:
                    self.send_error(404)
                    return
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.path != '/api/chat':
                    self.send_error(404)
                    return
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                assert request['model'] == 'fixture-ollama'
                question = request['messages'][-1]['content']
                if question == 'audit-next':
                    assert not any(m['role'] == 'assistant' and m['content'] == '合成答案被截断于此' for m in request['messages'])
                if question == 'audit-patient-B':
                    assert not any('audit-patient-A' in m['content'] for m in request['messages'])
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
                self.send_header('Transfer-Encoding', 'chunked')
                self.end_headers()

                def send(payload):
                    content = (json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8')
                    self.wfile.write(f'{len(content):X}\r\n'.encode() + content + b'\r\n')
                    self.wfile.flush()

                try:
                    send({'message': {'thinking': '合成思考开始'}, 'done': False})
                    time.sleep(0.04)
                    if question == 'audit-partial':
                        send({'message': {'content': '合成答案被截断于此'}, 'done': False})
                    elif question == 'audit-next':
                        send({'message': {'content': '历史未保留截断答案'}, 'done': False})
                        send({'done': True})
                    elif question == 'audit-empty':
                        send({'done': True})
                    elif question == 'audit-first-token':
                        send({'message': {'content': '首字已到达'}, 'done': False})
                        time.sleep(1.2)
                        send({'message': {'content': '，稍后完成'}, 'done': False})
                        send({'done': True})
                    elif question.startswith('audit-cancel'):
                        time.sleep(1.5)
                        self.connection.setblocking(False)
                        try:
                            open_socket = self.connection.recv(1, socket.MSG_PEEK) != b''
                        except BlockingIOError:
                            open_socket = True
                        finally:
                            self.connection.setblocking(True)
                        self.server.cancel_state['open_after_stop'] = open_socket
                        send({'message': {'content': '停止之后不应显示的输出'}, 'done': False})
                        send({'done': True})
                    else:
                        send({'message': {'content': '合成问答正常结束'}, 'done': False})
                        send({'done': True})
                    self.wfile.write(b'0\r\n\r\n')
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        class ThreadedServer(ThreadingMixIn, WSGIServer):
            daemon_threads = True

        class QuietHandler(WSGIRequestHandler):
            def log_message(self, *args):
                pass

        @contextmanager
        def running_server():
            if args.production:
                from waitress import create_server
                from serve import server_options
                options = server_options({'WAITRESS_HOST': '127.0.0.1', 'WAITRESS_PORT': '8080'})
                options['port'] = 0  # reserve an ephemeral local test port
                server = create_server(get_wsgi_application(), **options)
                thread = Thread(target=server.run, daemon=True)
                thread.start()
                try:
                    yield int(server.effective_port)
                finally:
                    server.close()
                    server.task_dispatcher.shutdown()
                    thread.join(timeout=2)
            else:
                with make_server('127.0.0.1', 0, StaticFilesHandler(get_wsgi_application()), server_class=ThreadedServer, handler_class=QuietHandler) as server:
                    Thread(target=server.serve_forever, daemon=True).start()
                    try:
                        yield server.server_port
                    finally:
                        server.shutdown()

        call_command('migrate', verbosity=0, interactive=False)
        user = get_user_model().objects.create_user('synthetic-user', password='synthetic-only')
        patients = [Patient.objects.create(name=f'合成患者{label}', bed_no=label) for label in ['A', 'B']]
        client = Client()
        client.force_login(user)
        with ThreadingHTTPServer(('127.0.0.1', 0), OllamaMock) as model:
            model.cancel_state = {}
            Thread(target=model.serve_forever, daemon=True).start()
            views._load_single_model_config = lambda: {'ollama_host': '127.0.0.1', 'ollama_port': model.server_port}
            views.RUNTIME_SESSIONS_DIR = fixture / 'runtime'
            views.PROMOTIONS_STATIC_DIR = fixture / 'static-fixture'
            with running_server() as server_port:
                try:
                    subprocess.run(
                        [os.environ.get('NODE_BINARY', 'node'), str(Path(__file__).with_suffix('.cjs'))],
                        input=json.dumps({
                            'base': f'http://127.0.0.1:{server_port}',
                            'production': args.production,
                            'session': client.cookies['sessionid'].value,
                            'model': f'http://127.0.0.1:{model.server_port}',
                            'patients': [p.id for p in patients],
                        }), text=True, timeout=100, check=True,
                    )
                    for question in ['audit-partial', 'audit-empty']:
                        row = QATiming.objects.get(qa_record=QARecord.objects.get(question=question))
                        assert row.status in {'interrupted', 'failed'}
                        assert row.client_total_ms is None
                    for question in ['audit-normal-True', 'audit-normal-False', 'audit-no-renderers']:
                        # JS bool formatting is lower-case.
                        question = question.replace('True', 'true').replace('False', 'false')
                        row = QATiming.objects.get(qa_record=QARecord.objects.get(question=question))
                        assert row.status == 'success'
                        assert row.client_total_ms is not None
                    assert QATiming.objects.filter(status='cancelled').count() >= 1
                    timing = QATiming.objects.get(qa_record=QARecord.objects.get(question='audit-first-token'))
                    assert timing.client_total_ms - timing.client_first_ms >= 900
                    print('PASS backend timing and browser measurements persisted consistently')
                finally:
                    model.shutdown()


if __name__ == '__main__':
    main()
