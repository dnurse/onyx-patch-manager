"""Production-conscious Django settings for an internal OPM installation.

Credentials and deployment settings are provided by the installer through an
owner-readable environment file. There are no hard-coded deployment secrets.
The same SQLite file houses existing OPM inventory and Django account tables.
Django migrates only its own tables and never owns the legacy OPM tables.
"""
from __future__ import annotations

import os
from pathlib import Path
from opm.config import Config

BASE_DIR = Path(__file__).resolve().parent
OPM_CONFIG = Config.load()
SECRET_KEY = os.environ.get('OPM_WEB_SECRET_KEY', '')
if len(SECRET_KEY) < 48:
    raise RuntimeError('OPM_WEB_SECRET_KEY must be set to a generated secret (48+ characters)')
DEBUG = False
ALLOWED_HOSTS = [host.strip() for host in os.environ.get('OPM_WEB_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if host.strip()]
CSRF_TRUSTED_ORIGINS = [x.strip() for x in os.environ.get('OPM_WEB_CSRF_TRUSTED_ORIGINS', '').split(',') if x.strip()]
INSTALLED_APPS = [
    'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages',
    'django.contrib.staticfiles', 'opm.web.apps.OPMWebConfig',
]
MIDDLEWARE = [
    'opm.web.access.InternalWebAccessMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'opm.web.middleware.OPMTimezoneMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
ROOT_URLCONF = 'opm.web.urls'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'], 'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.debug',
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
    ]},
}]
WSGI_APPLICATION = 'opm.web.wsgi.application'
DATABASES = {'default': {
    'ENGINE': 'django.db.backends.sqlite3',
    'NAME': str(OPM_CONFIG.database),
    'OPTIONS': {'timeout': 15},
}}
# Persistent SQLite is appropriate for a modest homelab. The existing
# controller enables WAL. Django uses short transactions for its own tables.
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 12}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
]
LANGUAGE_CODE = 'en-us'
TIME_ZONE = os.environ.get('OPM_WEB_TIME_ZONE', 'America/Toronto')
USE_TZ = True
STATIC_URL = '/static/'
STATIC_ROOT = OPM_CONFIG.base / 'web_static'
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'
SESSION_COOKIE_NAME = 'opm_session'
CSRF_COOKIE_NAME = 'opm_csrf'
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 60 * 60 * 8
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
# Loopback-only HTTP is useful for a local SSH tunnel. When publishing behind
# a trusted HTTPS proxy, explicitly enable secure cookies and proxy handling.
OPM_WEB_ACCESS_MODE = os.environ.get('OPM_WEB_ACCESS_MODE', 'legacy')
OPM_WEB_TRUSTED_PROXY_IPS = os.environ.get('OPM_WEB_TRUSTED_PROXY_IPS', '')
SESSION_COOKIE_SECURE = os.environ.get('OPM_WEB_SECURE_COOKIES', '0') == '1'
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_HSTS_SECONDS = 31536000 if os.environ.get('OPM_WEB_ENABLE_HSTS') == '1' else 0
X_FRAME_OPTIONS = 'DENY'
if os.environ.get('OPM_WEB_TRUST_PROXY') == '1':
    # Only enable when a trusted proxy strips client-supplied forwarded headers
    # and the Gunicorn listener is not accessible except from that proxy.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

STORAGES = {'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'}, 'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}}
