import logging
from django.contrib import admin
from django.conf import settings
from django.db import DatabaseError, connection
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import path, include, re_path
from django.views.static import serve
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

def svelte_app(request, path=''):
    if request.path.startswith('/app') and not request.user.is_authenticated:
        return redirect('/')
    index = settings.BASE_DIR / 'pos' / 'static' / 'frontend' / 'index.html'
    if not index.exists():
        return HttpResponse('Build the SvelteKit frontend with pnpm build.', status=503)
    response = FileResponse(index.open('rb'), content_type='text/html')
    # The compiled assets are content-hashed and cacheable, but the shell must
    # always point browsers at the latest build after a frontend deployment.
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

def login_entry(request):
    """Serve the SPA login at / and keep authenticated sessions under /app/."""
    if request.user.is_authenticated:
        return redirect('/app/')
    return svelte_app(request)

@require_GET
@never_cache
def healthz(request):
    # Unauthenticated uptime check: proves the app and database respond, reveals nothing else.
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
    except DatabaseError:
        logging.getLogger(__name__).exception('Health check database query failed')
        return JsonResponse({'status': 'unavailable'}, status=503)
    return JsonResponse({'status': 'ok'})

urlpatterns = [
    path('healthz', healthz),
    path(settings.ADMIN_URL, admin.site.urls),
    path('api/', include('pos.api_urls')),
    re_path(r'^app/(?P<path>_app/.*)$', serve,
            {'document_root': settings.BASE_DIR / 'pos' / 'static' / 'frontend'}),
    path('app/', svelte_app, name='svelte_app'),
    re_path(r'^app/(?P<path>.*)$', svelte_app),
    path('', login_entry, name='login_entry'),
    path('', include('pos.urls')),
]
urlpatterns += staticfiles_urlpatterns()
