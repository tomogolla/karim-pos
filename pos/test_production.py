import logging
import os
import subprocess
import sys
from unittest.mock import patch

from django.conf import settings
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase, override_settings

from config.log_redaction import RedactFilter

PRODUCTION_ENV = {
    'DJANGO_SETTINGS_MODULE': 'config.settings',
    'DJANGO_DEBUG': '0',
    'DJANGO_SECRET_KEY': os.environ.get('DJANGO_SECRET_KEY', 'production-secret-key'),
    'DJANGO_ALLOWED_HOSTS': os.environ.get('DJANGO_ALLOWED_HOSTS', 'pos.example.com'),
    'DJANGO_CSRF_TRUSTED_ORIGINS': os.environ.get('DJANGO_CSRF_TRUSTED_ORIGINS', 'https://pos.example.com'),
    'DJANGO_DB_NAME': os.environ.get('DJANGO_DB_NAME', 'pos'),
    'SHOPIFY_STORE': os.environ.get('SHOPIFY_STORE', 'https://example.com/store'),
}


def load_settings(**overrides):
    """Import the settings module in a fresh interpreter. Explicit values win over any local .env file."""
    env = {**os.environ, **PRODUCTION_ENV, **overrides}
    script = 'from django.conf import settings; print(settings.DEBUG, settings.DATABASES["default"]["ENGINE"])'
    return subprocess.run([sys.executable, '-c', script], cwd=settings.BASE_DIR, env=env,
                          capture_output=True, text=True, timeout=60)


class ProductionSettingsTests(SimpleTestCase):
    def test_production_settings_load(self):
        result = load_settings()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ['False', 'django.db.backends.postgresql'])

    def test_debug_is_off_unless_explicitly_enabled(self):
        for value in ['', 'true', 'yes', 'on']:
            result = load_settings(DJANGO_DEBUG=value)
            self.assertEqual(result.stdout.split()[:1], ['False'], value)

    def test_missing_production_settings_fail_closed(self):
        for name, value in [('DJANGO_SECRET_KEY', ''), ('DJANGO_ALLOWED_HOSTS', ''),
                            ('DJANGO_CSRF_TRUSTED_ORIGINS', ''), ('DJANGO_CSRF_TRUSTED_ORIGINS', 'pos.example.com'),
                            ('DJANGO_DB_NAME', ''), ('SHOPIFY_STORE', 'https://example.com/store')]:
            result = load_settings(**{name: value})
            self.assertNotEqual(result.returncode, 0, name)
            self.assertIn('ImproperlyConfigured', result.stderr, name)
            self.assertIn(name, result.stderr.splitlines()[-1], name)

    def test_debug_falls_back_to_sqlite(self):
        result = load_settings(DJANGO_DEBUG='1', DJANGO_SECRET_KEY='', DJANGO_ALLOWED_HOSTS='',
                               DJANGO_CSRF_TRUSTED_ORIGINS='', DJANGO_DB_NAME='')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ['True', 'django.db.backends.sqlite3'])


class LogRedactionTests(SimpleTestCase):
    def render(self, msg, *args, exc_info=None):
        record = logging.LogRecord('pos', logging.ERROR, __file__, 1, msg, args, exc_info)
        RedactFilter().filter(record)
        return logging.Formatter('%(message)s').format(record)

    @override_settings(SHOPIFY_ACCESS_TOKEN='configured-access-token-value')
    def test_secrets_and_contact_details_are_redacted(self):
        line = self.render('token=%s header %s mail %s phone %s local %s password: %s',
                           'abc123', 'configured-access-token-value', 'amina@example.com',
                           '+254 712 345 678', '0712345678', 'hunter22')
        for secret in ['abc123', 'configured-access-token-value', 'amina@example.com', '712 345 678', '0712345678', 'hunter22']:
            self.assertNotIn(secret, line)

    def test_shopify_token_in_traceback_is_redacted(self):
        token = ''.join(('shpat_', '0123456789abcdef', '0123456789abcdef'))
        try:
            raise ValueError(f'bad token {token}')
        except ValueError:
            line = self.render('Request failed', exc_info=sys.exc_info())
        self.assertIn('Request failed', line)
        self.assertNotIn(token[:22], line)

    def test_ordinary_log_lines_are_unchanged(self):
        line = 'Order gid://shopify/Order/5551234567890 synced at 2026-11-02 14:44:01 for KR-00012'
        self.assertEqual(self.render(line), line)


class HealthCheckTests(TestCase):
    def test_healthz_is_public_and_minimal(self):
        response = self.client.get('/healthz')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        self.assertIn('no-cache', response['Cache-Control'])

    def test_healthz_reports_database_failure_without_details(self):
        with patch('config.urls.connection') as connection, self.assertLogs('config.urls', 'ERROR'):
            connection.cursor.side_effect = DatabaseError('could not connect to server at db.internal')
            response = self.client.get('/healthz')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'unavailable'})
