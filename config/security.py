"""Hooks used by django-axes for client IP detection and lockout responses."""
import ipaddress

from django.conf import settings
from django.http import HttpResponse, JsonResponse


def client_ip(request):
    """Return the address of the client that connected to nginx.

    Behind the proxy, nginx appends the real client address as the right-most
    X-Forwarded-For entry, so only that entry is trusted; anything a client put
    further left is ignored. Without DJANGO_BEHIND_PROXY the header is never read.
    """
    remote_addr = request.META.get('REMOTE_ADDR', '')
    if not settings.BEHIND_PROXY:
        return remote_addr
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[-1].strip()
    try:
        return str(ipaddress.ip_address(forwarded))
    except ValueError:
        return remote_addr


def lockout_response(request, original_response=None, credentials=None):
    seconds = int(settings.AXES_COOLOFF_TIME.total_seconds())
    message = (f'Too many failed sign-in attempts. Try again in {seconds // 60} minutes '
               'or ask an administrator to unlock the account.')
    if request.path.startswith('/api/'):
        response = JsonResponse({'error': message, 'locked': True}, status=429)
    else:
        response = HttpResponse(message, status=429, content_type='text/plain; charset=utf-8')
    response['Retry-After'] = str(seconds)
    return response
