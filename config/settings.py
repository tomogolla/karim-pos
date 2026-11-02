import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key
BASE_DIR = Path(__file__).resolve().parent.parent
if (BASE_DIR / '.env').exists():
    for line in (BASE_DIR / '.env').read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip().strip('\"').strip("'"))
DEBUG = os.getenv('DJANGO_DEBUG', '1') == '1'
DEMO_MODE = os.getenv('DEMO_MODE', '1' if DEBUG else '0') == '1'
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', '')
if not SECRET_KEY:
    if not DEBUG: raise ImproperlyConfigured('Set DJANGO_SECRET_KEY in production.')
    secret_path = BASE_DIR / '.local-secret'
    if not secret_path.exists(): secret_path.write_text(get_random_secret_key())
    SECRET_KEY = secret_path.read_text()
ALLOWED_HOSTS = os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,[::1]').split(',')
INSTALLED_APPS = ['django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'pos']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [], 'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.request', 'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages', 'pos.context.common']}}]
WSGI_APPLICATION = 'config.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DJANGO_DB_NAME'),       # The name of the database you created
        'USER': os.getenv('DJANGO_DB_USER'),                  # Your database user
        'PASSWORD': os.getenv('DJANGO_DB_PASSWORD'),   # Your database password
        'HOST': os.getenv('DJANGO_DB_HOST', 'localhost'),                # Use '127.0.0.1' or your Droplet IP if remote
        'PORT': os.getenv('DJANGO_DB_PORT', '5432'),                     # Default PostgreSQL port
    }
}

AUTH_PASSWORD_VALIDATORS = [{'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'}, {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'}]
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
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
POS_CURRENCY = os.getenv('POS_CURRENCY', 'KES')
SHOPIFY_STORE = os.getenv('SHOPIFY_STORE', '')
SHOPIFY_ACCESS_TOKEN = os.getenv('SHOPIFY_ACCESS_TOKEN', '')
SHOPIFY_API_VERSION = '2026-07'
SHOPIFY_LIVE_SYNC = os.getenv('SHOPIFY_LIVE_SYNC', '0') == '1'
SHOPIFY_INVENTORY_BEHAVIOUR = os.getenv('SHOPIFY_INVENTORY_BEHAVIOUR', 'DECREMENT_OBEYING_POLICY')
