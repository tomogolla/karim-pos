from django.contrib import admin
from django.conf import settings
from django.http import FileResponse, HttpResponse
from django.urls import path, include, re_path
from django.views.static import serve
from django.contrib.staticfiles.urls import staticfiles_urlpatterns

def svelte_app(request, path=''):
    index = settings.BASE_DIR / 'pos' / 'static' / 'frontend' / 'index.html'
    if not index.exists():
        return HttpResponse('Build the SvelteKit frontend with pnpm build.', status=503)
    return FileResponse(index.open('rb'), content_type='text/html')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('pos.api_urls')),
    re_path(r'^app/(?P<path>_app/.*)$', serve,
            {'document_root': settings.BASE_DIR / 'pos' / 'static' / 'frontend'}),
    path('app/', svelte_app, name='svelte_app'),
    re_path(r'^app/(?P<path>.*)$', svelte_app),
    path('', svelte_app),
    path('', include('pos.urls')),
]
urlpatterns += staticfiles_urlpatterns()
