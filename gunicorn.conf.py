"""Gunicorn settings. It listens on localhost only; nginx proxies public HTTPS traffic to it.

Run with: gunicorn config.wsgi:application -c gunicorn.conf.py
"""
import os

bind = f"127.0.0.1:{os.getenv('GUNICORN_PORT', '8765')}"
workers = 3
timeout = 60
accesslog = '-'
errorlog = '-'
# %(U)s is the path without the query string: customer searches put names,
# phone numbers and emails in ?q=, and those must not reach the logs.
access_log_format = '%(h)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s %(M)sms'
