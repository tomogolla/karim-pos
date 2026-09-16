"""Shopify boundary. No payment collection occurs here; payments are recorded externally."""
import json
import re
from urllib.request import Request, urlopen
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from .models import Product, SyncJob, Audit

CATALOG = '''query Catalog($after: String) {
  shop { currencyCode }
  productVariants(first: 100, after: $after) {
    nodes { id title sku price inventoryQuantity taxable
      inventoryItem { tracked requiresShipping }
      product { title status productType tags }
    }
    pageInfo { hasNextPage endCursor }
  }
}'''
CREATE_ORDER = '''mutation CreateOrder($order: OrderCreateOrderInput!, $options: OrderCreateOptionsInput) {
  orderCreate(order: $order, options: $options) {
    order { id name }
    userErrors { field message }
  }
}'''

def graphql(query, variables):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9-]*\.myshopify\.com', settings.SHOPIFY_STORE):
        raise ValueError('Set SHOPIFY_STORE to your store.myshopify.com domain.')
    if not settings.SHOPIFY_ACCESS_TOKEN: raise ValueError('Shopify access token is not configured.')
    req = Request(f'https://{settings.SHOPIFY_STORE}/admin/api/{settings.SHOPIFY_API_VERSION}/graphql.json',
                  data=json.dumps({'query': query, 'variables': variables}).encode(),
                  headers={'Content-Type': 'application/json', 'X-Shopify-Access-Token': settings.SHOPIFY_ACCESS_TOKEN})
    with urlopen(req, timeout=25) as response:
        result = json.load(response)
    if result.get('errors'): raise ValueError('Shopify returned GraphQL errors. Check app permissions and API compatibility.')
    return result['data']

def pull_catalog():
    # Stage all pages before replacing the cache; failed pulls leave the last good catalog intact.
    after, staged = None, []
    while True:
        data = graphql(CATALOG, {'after': after})
        if data['shop']['currencyCode'] != settings.POS_CURRENCY:
            raise ValueError('Store currency differs from POS_CURRENCY. Align settings before importing.')
        page = data['productVariants']
        staged.extend(page['nodes'])
        if not page['pageInfo']['hasNextPage']: break
        after = page['pageInfo']['endCursor']
    with transaction.atomic():
        Product.objects.filter(demo=False).update(active=False)
        for node in staged:
            product, item = node['product'], node['inventoryItem']
            service = 'service' in [t.lower() for t in product['tags']] or product['productType'].lower() == 'service'
            title = product['title'] + (f" / {node['title']}" if node['title'] != 'Default Title' else '')
            Product.objects.update_or_create(variant_id=node['id'], defaults={
                'title': title, 'sku': node['sku'] or '', 'price': node['price'],
                'category': product['productType'] or ('Services' if service else 'Retail'),
                'kind': 'service' if service else 'retail', 'tracked': item['tracked'],
                'inventory': node['inventoryQuantity'], 'taxable': node['taxable'],
                'active': product['status'] == 'ACTIVE', 'synced_at': timezone.now(), 'demo': False})
        Product.objects.filter(demo=True).update(active=False)
    return len(staged)

def money(value, currency):
    return {'shopMoney': {'amount': str(value), 'currencyCode': currency}}

def build_payload(bill):
    lines = []
    for line in bill.lines.select_related('product', 'staff'):
        props = {'Staff': line.staff_name or (line.staff.name if line.staff else ''),
                 'Staff ID': str(line.staff_id or ''), 'Original unit price': str(line.price),
                 'Discount per item': str(line.discount), 'Line total': str(line.total)}
        item = {'title': line.title, 'quantity': line.quantity,
                'priceSet': money(line.price-line.discount, bill.currency),
                'properties': [{'name': k, 'value': v} for k, v in props.items()],
                'requiresShipping': False, 'taxable': line.product.taxable, 'taxLines': []}
        if not line.product.demo: item['variantId'] = line.product.variant_id
        lines.append(item)
    customer = {'firstName': bill.first_name, 'lastName': bill.last_name}
    if bill.email: customer['email'] = bill.email
    if bill.phone: customer['phone'] = bill.phone
    customer_input = {'toUpsert': customer}
    if bill.shopify_customer_id:
        if bill.shopify_customer.store != settings.SHOPIFY_STORE.lower().strip():
            raise ValueError('The selected customer belongs to a different Shopify store. Select a customer from the current store before checkout.')
        customer_input = {'toAssociate': {'id': bill.shopify_customer.shopify_id}}
    return {'order': {'name': bill.name, 'currency': bill.currency,
        'processedAt': bill.paid_at.isoformat(), 'sourceIdentifier': str(bill.uid),
        'tags': ['karim-pos'], 'note': bill.notes,
        'customer': customer_input, 'lineItems': lines,
        'customAttributes': [{'key': 'Karim POS ID', 'value': str(bill.uid)},
                             {'key': 'Created at', 'value': bill.created_at.isoformat()},
                             {'key': 'Payment reference', 'value': bill.payment.reference},
                             {'key': 'Payment method', 'value': bill.payment.method}],
        'transactions': [{'kind': 'SALE', 'status': 'SUCCESS', 'gateway': 'manual',
                          'amountSet': money(bill.total_paid, bill.currency),
                          'authorizationCode': bill.payment.reference}],
        'taxesIncluded': False},
        'options': {'inventoryBehaviour': settings.SHOPIFY_INVENTORY_BEHAVIOUR, 'sendReceipt': False, 'sendFulfillmentReceipt': False}}

def send_job(job_id):
    if not settings.SHOPIFY_LIVE_SYNC: raise ValueError('Live order sync is disabled. Connect and validate your store first.')
    if not settings.SHOPIFY_ACCESS_TOKEN: raise ValueError('Shopify credentials are not configured.')
    job = SyncJob.objects.select_related('bill').get(pk=job_id)
    if job.bill.lines.filter(product__demo=True).exists(): raise ValueError('Demo orders cannot be sent to Shopify.')
    # Explicit Shopify user errors mean no order was created. Rebuild those
    # failed payloads so corrected payload generation is applied on retry.
    # Pending jobs retain the immutable payload captured at checkout, while
    # uncertain outcomes remain in review and are never claimed below.
    retry_payload = build_payload(job.bill) if job.state == 'failed' else job.payload
    # Atomic claim prevents concurrent workers sending the same order.
    claimed = SyncJob.objects.filter(pk=job_id, state__in=['pending', 'failed']).update(state='sending', attempts=F('attempts')+1, updated_at=timezone.now())
    if not claimed: return False
    if job.state == 'failed':
        SyncJob.objects.filter(pk=job_id).update(payload=retry_payload)
    try:
        result = graphql(CREATE_ORDER, retry_payload)['orderCreate']
        if result.get('userErrors'):
            SyncJob.objects.filter(pk=job_id).update(state='failed', error=json.dumps(result['userErrors']), updated_at=timezone.now())
            return False
        shopify_id = result['order']['id']
        SyncJob.objects.filter(pk=job_id).update(state='synced', shopify_id=shopify_id, error='', updated_at=timezone.now())
        Audit.objects.create(bill=job.bill, event='Order synchronized to Shopify')
        if job.bill.shopify_customer_id:
            # Refresh the shared cache after sync, without turning a successful
            # order submission into a retry if the subsequent read fails.
            try:
                from .customers import refresh_customer
                refresh_customer(job.bill.shopify_customer)
            except Exception:
                Audit.objects.create(bill=job.bill, event='Order synced; customer history refresh deferred')
        return True
    except Exception:
        # Timeout/error can happen AFTER Shopify commits. Never blindly resend.
        SyncJob.objects.filter(pk=job_id).update(state='review', error='Outcome uncertain. Check Shopify using the Karim POS ID before taking further action. Automatic resend blocked.', updated_at=timezone.now())
        return False
