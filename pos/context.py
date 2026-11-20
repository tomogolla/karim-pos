from django.conf import settings
from .shopify import credentials_configured

def common(request):
    return {'currency': settings.POS_CURRENCY, 'shopify_configured': credentials_configured(), 'live_sync': settings.SHOPIFY_LIVE_SYNC}
