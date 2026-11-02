from django.conf import settings
def common(request):
    return {'currency': settings.POS_CURRENCY, 'shopify_configured': bool(settings.SHOPIFY_STORE and settings.SHOPIFY_ACCESS_TOKEN), 'live_sync': settings.SHOPIFY_LIVE_SYNC}
