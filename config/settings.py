import os
import re
from datetime import timedelta
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key
BASE_DIR = Path(__file__).resolve().parent.parent
if (BASE_DIR / '.env').exists():
    for line in (BASE_DIR / '.env').read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip().strip('\"').strip("'"))

def env(name, default=''):
    return os.getenv(name, default).strip()

def env_flag(name):
    return env(name) == '1'

def env_int(name, default):
    try:
        return int(env(name) or default)
    except ValueError:
        raise ImproperlyConfigured(f'{name} must be a whole number.')

def env_list(name):
    return [item.strip() for item in env(name).split(',') if item.strip()]

# Fail closed: debug mode only when DJANGO_DEBUG=1 is set explicitly.
DEBUG = env_flag('DJANGO_DEBUG')
SECRET_KEY = env('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    if not DEBUG: raise ImproperlyConfigured('Set DJANGO_SECRET_KEY in production.')
    secret_path = BASE_DIR / '.local-secret'
    if not secret_path.exists(): secret_path.write_text(get_random_secret_key())
    SECRET_KEY = secret_path.read_text()
ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS')
if not ALLOWED_HOSTS:
    if not DEBUG: raise ImproperlyConfigured('Set DJANGO_ALLOWED_HOSTS (comma-separated, e.g. pos.example.com) in production.')
    ALLOWED_HOSTS = ['localhost', '127.0.0.1', '[::1]']
CSRF_TRUSTED_ORIGINS = env_list('DJANGO_CSRF_TRUSTED_ORIGINS')
if not DEBUG:
    if not CSRF_TRUSTED_ORIGINS:
        raise ImproperlyConfigured('Set DJANGO_CSRF_TRUSTED_ORIGINS (comma-separated, e.g. https://pos.example.com) in production.')
    if any(not origin.startswith('https://') for origin in CSRF_TRUSTED_ORIGINS):
        raise ImproperlyConfigured('Every DJANGO_CSRF_TRUSTED_ORIGINS entry must start with https://.')
INSTALLED_APPS = ['django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'axes', 'pos']
# AxesMiddleware must stay last so it can replace the response of a locked-out login.
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware', 'axes.middleware.AxesMiddleware']
AUTHENTICATION_BACKENDS = ['axes.backends.AxesStandaloneBackend', 'django.contrib.auth.backends.ModelBackend']
ROOT_URLCONF = 'config.urls'
ADMIN_URL = env('DJANGO_ADMIN_URL', 'admin/').strip('/') + '/'
if ADMIN_URL == '/': raise ImproperlyConfigured('DJANGO_ADMIN_URL cannot be empty.')
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [], 'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.request', 'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages', 'pos.context.common']}}]
WSGI_APPLICATION = 'config.wsgi.application'

# PostgreSQL whenever DJANGO_DB_NAME is set. SQLite is only a local development fallback.
if env('DJANGO_DB_NAME'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': env('DJANGO_DB_NAME'),
            'USER': env('DJANGO_DB_USER'),
            'PASSWORD': env('DJANGO_DB_PASSWORD'),
            'HOST': env('DJANGO_DB_HOST', 'localhost'),
            'PORT': env('DJANGO_DB_PORT', '5432'),
            'CONN_MAX_AGE': 60,
            'CONN_HEALTH_CHECKS': True,
        }
    }
elif DEBUG:
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3'}}
else:
    raise ImproperlyConfigured('Set DJANGO_DB_NAME and the other DJANGO_DB_* settings in production.')

AUTH_PASSWORD_VALIDATORS = [{'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'}, {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 12}}, {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'}, {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'}]
LANGUAGE_CODE = 'en-us'
TIME_ZONE = os.getenv('POS_TIME_ZONE', 'Africa/Nairobi')
USE_I18N = True
USE_TZ = True
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/app/'
LOGOUT_REDIRECT_URL = '/login/'

# Cookies. The SvelteKit client reads the csrftoken cookie, so it must not be HttpOnly.
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_FAILURE_VIEW = 'config.security.csrf_failure'
SESSION_COOKIE_AGE = 12 * 60 * 60
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'
# nginx terminates HTTPS. Trust X-Forwarded-Proto only when nginx is known to set it.
# The Host header is passed through by nginx, so X-Forwarded-Host is not trusted.
BEHIND_PROXY = env_flag('DJANGO_BEHIND_PROXY')
if BEHIND_PROXY:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = False
# nginx already redirects HTTP to HTTPS. Enabling this without the forwarded-proto
# header causes a redirect loop, so it is off unless explicitly requested.
SECURE_SSL_REDIRECT = not DEBUG and env_flag('DJANGO_SECURE_SSL_REDIRECT')
# Keep HSTS at 0 until HTTPS is confirmed working, then raise it (e.g. 31536000).
SECURE_HSTS_SECONDS = 0 if DEBUG else env_int('DJANGO_SECURE_HSTS_SECONDS', 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_flag('DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS')

# Login brute-force protection: lock a username + IP pair after repeated failures.
AXES_FAILURE_LIMIT = env_int('AXES_FAILURE_LIMIT', 5)
AXES_COOLOFF_TIME = timedelta(minutes=env_int('AXES_COOLOFF_MINUTES', 30))
AXES_LOCKOUT_PARAMETERS = [['username', 'ip_address']]
AXES_RESET_ON_SUCCESS = True
AXES_CLIENT_IP_CALLABLE = 'config.security.client_ip'
AXES_LOCKOUT_CALLABLE = 'config.security.lockout_response'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {'redact': {'()': 'config.log_redaction.RedactFilter'}},
    'formatters': {'plain': {'format': '{asctime} {levelname} {name}: {message}', 'style': '{'}},
    'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'plain', 'filters': ['redact']}},
    'root': {'handlers': ['console'], 'level': env('DJANGO_LOG_LEVEL', 'INFO')},
    'loggers': {'django': {'handlers': ['console'], 'level': env('DJANGO_LOG_LEVEL', 'INFO'), 'propagate': False},
                # Axes logs failed logins and lockouts as warnings; its info lines are routine noise.
                'axes': {'level': 'WARNING'}},
}

POS_CURRENCY = os.getenv('POS_CURRENCY', 'KES')
SHOPIFY_STORE = os.getenv('SHOPIFY_STORE', '')
SHOPIFY_ACCESS_TOKEN = os.getenv('SHOPIFY_ACCESS_TOKEN', '')
SHOPIFY_API_VERSION = '2026-07'
SHOPIFY_LIVE_SYNC = os.getenv('SHOPIFY_LIVE_SYNC', '0') == '1'
SHOPIFY_INVENTORY_BEHAVIOUR = os.getenv('SHOPIFY_INVENTORY_BEHAVIOUR', 'DECREMENT_OBEYING_POLICY')
if not DEBUG and SHOPIFY_STORE and not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9-]*\.myshopify\.com', SHOPIFY_STORE):
    raise ImproperlyConfigured('SHOPIFY_STORE must be your store.myshopify.com domain (no https://, no path).')
