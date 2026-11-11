"""Production configuration for the single-process Windows Waitress service.

Secrets and site-specific values are loaded by settings.py from the private
RUIJIN_CONFIG_FILE. Static finders keep the existing asset layout intact.
"""
import os
from pathlib import PurePosixPath
from urllib.parse import unquote

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponseNotFound
from whitenoise.middleware import WhiteNoiseMiddleware

# Reject debug before importing the base module: its development fallback
# generates a temporary key, which must never become a production signing key.
if os.environ.get('DJANGO_DEBUG', '0').lower() in ('1', 'true', 'yes'):
    raise ImproperlyConfigured('Production entry point refuses DJANGO_DEBUG; use a private SECRET_KEY.')


from .settings import *  # noqa: F403 - preserve the deployment's application settings


DEBUG = False
if not isinstance(ALLOWED_HOSTS, (list, tuple)) or not ALLOWED_HOSTS or any(
    not isinstance(host, str) or not host.strip() or '*' in host for host in ALLOWED_HOSTS
):
    raise ImproperlyConfigured('Production ALLOWED_HOSTS must contain explicit host names or addresses.')
if not SECRET_KEY or len(SECRET_KEY) < 32 or SECRET_KEY.startswith('django-insecure-'):
    raise ImproperlyConfigured('Production requires a private random SECRET_KEY of at least 32 characters.')
CORS_ALLOW_ALL_ORIGINS = False
OLLAMA_READ_TIMEOUT_SECONDS = float(os.environ.get('OLLAMA_READ_TIMEOUT_SECONDS', '900'))
if not 1 <= OLLAMA_READ_TIMEOUT_SECONDS <= 86400:
    raise ImproperlyConfigured('OLLAMA_READ_TIMEOUT_SECONDS must be between 1 and 86400.')


_PRIVATE_STATIC_NAMES = frozenset({
    'input.txt', 'latest_response.txt', 'single_model_config.json',
    'tts_offline_sample', 'production.json', 'settings_local.json',
    'role.txt', 'knowledge.txt', 'knowledge_updated.txt',
})
_PRIVATE_STATIC_DIRS = frozenset({'runtime_sessions', '__pycache__', 'msc', 'logs', '.git'})
_PRIVATE_STATIC_SUFFIXES = frozenset({
    '.py', '.pyc', '.pyo', '.sqlite', '.sqlite3', '.db', '.log', '.cfg', '.ini',
    '.pem', '.key', '.pfx', '.p12', '.exe', '.dll', '.bat', '.cmd', '.ps1', '.bak',
})


def private_static_path(path, prefix='/static/'):
    """Block legacy runtime/config files accidentally stored beside public assets."""
    normalized = path
    for _ in range(4):
        decoded = unquote(normalized)
        if decoded == normalized:
            break
        normalized = decoded
    normalized = normalized.replace('\\', '/').casefold()
    prefix = prefix.casefold()
    if not normalized.startswith(prefix):
        return False
    parts = normalized[len(prefix):].split('/')
    # Windows resolves trailing dots/spaces as aliases of the original path.
    # Reject aliases, including DOS 8.3 names, before static finders open files.
    if any(part.startswith('.') or part.endswith(('.', ' ')) or ':' in part or '~' in part
           or any(ord(char) < 32 or ord(char) == 127 for char in part)
           or part in _PRIVATE_STATIC_DIRS for part in parts):
        return True
    name = parts[-1]
    for compression in ('.gz', '.br'):
        if name.endswith(compression):
            name = name[:-len(compression)]
    return name in _PRIVATE_STATIC_NAMES or PurePosixPath(name).suffix in _PRIVATE_STATIC_SUFFIXES


class RestrictedWhiteNoiseMiddleware(WhiteNoiseMiddleware):
    """Serve public assets without exposing legacy responses or local configuration."""

    def __call__(self, request):
        # Static responses bypass Django's CommonMiddleware; validate Host here.
        request.get_host()
        if private_static_path(request.path_info, self.static_prefix):
            response = HttpResponseNotFound()
            response['Cache-Control'] = 'no-store'
            return response
        return super().__call__(request)


def static_response_headers(headers, path, url):
    # WhiteNoise returns before UnitySharedArrayBufferMiddleware is reached.
    if '/unity/' in url.casefold():
        headers['Cross-Origin-Opener-Policy'] = 'same-origin'
        headers['Cross-Origin-Embedder-Policy'] = 'require-corp'
    # Generated audio may reuse a filename. Do not replay a cached prior answer.
    if PurePosixPath(url).suffix.lower() in {'.wav', '.mp3', '.m4a', '.ogg'}:
        headers['Cache-Control'] = 'no-store'


MIDDLEWARE = [entry for entry in MIDDLEWARE if entry != 'whitenoise.middleware.WhiteNoiseMiddleware']
_security = 'django.middleware.security.SecurityMiddleware'
_position = MIDDLEWARE.index(_security) + 1 if _security in MIDDLEWARE else 0
MIDDLEWARE.insert(_position, 'mysite.production_settings.RestrictedWhiteNoiseMiddleware')

# The legacy speech helpers can create/replace assets after service startup.
# Keep finder refresh enabled for compatibility, with private paths explicitly
# rejected above. No static root or arbitrary filesystem root is exposed.
WHITENOISE_ROOT = None
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = True
WHITENOISE_MAX_AGE = 60
WHITENOISE_ADD_HEADERS_FUNCTION = static_response_headers
WHITENOISE_MIMETYPES = {'.wasm': 'application/wasm', '.data': 'application/octet-stream'}
