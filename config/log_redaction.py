"""Logging filter that keeps secrets and customer contact details out of log output."""
import logging
import re

from django.conf import settings

PATTERNS = [
    # Shopify Admin API tokens (shpat_, shpca_, shppa_, shpss_ followed by hex).
    (re.compile(r'shp(?:at|ca|pa|ss)_[0-9a-fA-F]{16,}'), '[redacted token]'),
    # key=value / "key": "value" pairs whose name suggests a secret.
    (re.compile(r'''(?i)((?:password|passwd|secret|token|api[_-]?key|authorization)["']?\s*[:=]\s*["']?)[^\s"',;&}]+'''), r'\1[redacted]'),
    (re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+'), '[redacted email]'),
    # International (+254 7xx ...) and Kenyan local (07xxxxxxxx / 01xxxxxxxx) phone numbers.
    (re.compile(r'\+\d(?:[\s-]?\d){7,14}'), '[redacted phone]'),
    (re.compile(r'(?<![\w/.-])0[17]\d{8}(?!\d)'), '[redacted phone]'),
]


def redact(text):
    secrets = (getattr(settings, 'SHOPIFY_ACCESS_TOKEN', ''), getattr(settings, 'SECRET_KEY', ''),
               settings.DATABASES.get('default', {}).get('PASSWORD', ''))
    for value in secrets:
        if value and len(value) >= 8:
            text = text.replace(value, '[redacted]')
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactFilter(logging.Filter):
    """Render the message and any traceback once, redacted, before a handler formats it."""

    def filter(self, record):
        try:
            record.msg = redact(record.getMessage())
            record.args = None
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = redact(record.exc_text)
            if record.stack_info:
                record.stack_info = redact(record.stack_info)
        except Exception:
            record.msg, record.args = 'Log message could not be redacted and was suppressed.', None
        return True
