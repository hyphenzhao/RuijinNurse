from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = 'synthetic-local-test-only-not-a-production-secret-20261111'
DEBUG = True
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
ROOT_URLCONF = 'mysite.urls'
LOGIN_URL = '/accounts/login/'
LOGIN_EXEMPT_URLS = [r'^api/v1/', r'^static/']
INSTALLED_APPS = [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
    'rest_framework', 'promotions',
]
MIDDLEWARE = [
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'promotions.middleware.LoginRequiredMiddleware',
]
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'APP_DIRS': True,
    'DIRS': [],
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
    ]},
}]
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
REST_FRAMEWORK = {'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework_simplejwt.authentication.JWTAuthentication']}
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
STATIC_URL = '/static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
TIME_ZONE = 'Asia/Shanghai'
USE_TZ = True

WHISPER_HOST = PIPER_HOST = '127.0.0.1'
WHISPER_PORT = '8001'
PIPER_PORT = '8002'
