"""Run RuijinNurse with a single Waitress process on Windows or Linux.

Use the existing venv Python: python serve.py. Deployment configuration lives
outside the checkout. --check validates configuration without serving or
changing timing records.
"""
import argparse
from contextlib import contextmanager
import os
from pathlib import Path


def server_options(environ=None):
    env = os.environ if environ is None else environ
    port = int(env.get('WAITRESS_PORT', '80'))
    threads = int(env.get('WAITRESS_THREADS', '16'))
    if not 1 <= port <= 65535:
        raise ValueError('WAITRESS_PORT must be between 1 and 65535')
    if not 4 <= threads <= 64:
        raise ValueError('WAITRESS_THREADS must be between 4 and 64')
    return {
        'host': env.get('WAITRESS_HOST', '0.0.0.0'), 'port': port,
        'threads': threads,
        'channel_timeout': max(960, int(float(env.get('OLLAMA_READ_TIMEOUT_SECONDS', '900'))) + 60),
        'cleanup_interval': 10,
        'channel_request_lookahead': 1, 'expose_tracebacks': False,
        'ident': 'RuijinNurse',
    }


def lock_path():
    configured = os.environ.get('RUIJIN_SERVER_LOCKFILE')
    if configured:
        return Path(configured)
    if os.environ.get('RUIJIN_CONFIG_FILE'):
        return Path(os.environ['RUIJIN_CONFIG_FILE']).resolve().parent / 'server.lock'
    if os.name == 'nt' and os.environ.get('LOCALAPPDATA'):
        return Path(os.environ['LOCALAPPDATA']) / 'RuijinNurse' / 'server.lock'
    return Path(__file__).resolve().parent / '.runtime' / 'server.lock'


@contextmanager
def single_process_lock(path):
    """Keep one process per deployment; the timing cancellation registry is local."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('Another RuijinNurse service process is already running.') from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Validate configuration without starting the service')
    args = parser.parse_args(argv)
    # This entry point always uses production settings; local tests can use manage.py.
    os.environ['DJANGO_SETTINGS_MODULE'] = 'mysite.production_settings'
    from django.core.management import call_command
    from django.core.wsgi import get_wsgi_application
    from waitress import create_server

    application = get_wsgi_application()
    options = server_options()
    call_command('check', verbosity=0)
    if args.check:
        print('Production configuration check passed.')
        return
    with single_process_lock(lock_path()):
        # Reserve the port first. A bind failure must not modify live timings.
        # create_server does not accept/process requests until run() starts.
        server = create_server(application, **options)
        try:
            call_command('recover_question_timings', older_than_minutes=0)
            server.run()
        finally:
            server.close()
            dispatcher = getattr(server, 'task_dispatcher', None)
            if dispatcher is not None:
                dispatcher.shutdown()


if __name__ == '__main__':
    main()
