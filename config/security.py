"""Hooks for django-axes (client IP, lockout response) and the CSRF failure view."""
import ipaddress

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views import csrf


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


def csrf_failure(request, reason=''):
    """JSON for the SvelteKit API so the client can refresh its token and retry; Django's page elsewhere."""
    if request.path.startswith('/api/'):
        return JsonResponse({'error': 'Your security token expired. Reload the page and try again.', 'csrf': True}, status=403)
    return csrf.csrf_failure(request, reason)
