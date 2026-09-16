"""Shopify customer search, shared latest-order cache, and guarded creation."""
import re
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import Customer, CustomerCreation, Bill, Audit
from .shopify import graphql

CUSTOMER_FIELDS = '''
    id firstName lastName displayName
    defaultEmailAddress { emailAddress }
    defaultPhoneNumber { phoneNumber }
    lastOrder {
      id processedAt
      currentTotalPriceSet { shopMoney { amount currencyCode } }
    }
'''
SEARCH_CUSTOMERS = '''query SearchCustomers($query: String!, $after: String) {
  customers(first: 25, query: $query, after: $after, sortKey: NAME) {
    nodes { ''' + CUSTOMER_FIELDS + ''' }
    pageInfo { hasNextPage endCursor }
  }
}'''
GET_CUSTOMER = '''query CustomerDetails($id: ID!) {
  customer(id: $id) { ''' + CUSTOMER_FIELDS + ''' }
}'''
# Keep mutation response minimal: missing order-read permission must not obscure
# successful creation. New customers have no orders, so lastOrder starts as null.
CREATE_CUSTOMER = '''mutation CreateCustomer($input: CustomerInput!) {
  customerCreate(input: $input) {
    customer { id firstName lastName displayName
      defaultEmailAddress { emailAddress }
      defaultPhoneNumber { phoneNumber }
    }
    userErrors { field message }
  }
}'''

def store_domain():
    return settings.SHOPIFY_STORE.lower().strip()

def require_connection():
    if not settings.SHOPIFY_ACCESS_TOKEN or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9-]*\.myshopify\.com', settings.SHOPIFY_STORE):
        raise ValueError('Connect Shopify in the server settings to search or create customers.')

def search_expression(term):
    """Quote user input so Shopify search operators remain literal values."""
    term = term.strip()
    if not 2 <= len(term) <= 120:
        raise ValueError('Enter between 2 and 120 characters to search customers.')
    def quote(value):
        return '"' + re.sub(r'([\\":()])', r'\\\1', value) + '"'
    if '@' in term:
        return 'email:' + quote(term)
    if re.fullmatch(r'[+\d\s().-]+', term):
        return 'phone:' + quote(re.sub(r'[\s().-]', '', term))
    return ' AND '.join(f'(first_name:{quote(part)} OR last_name:{quote(part)})' for part in term.split())

def cache_customer(node):
    order = node.get('lastOrder') or {}
    money = (order.get('currentTotalPriceSet') or {}).get('shopMoney') or {}
    processed_at = parse_datetime(order['processedAt']) if order.get('processedAt') else None
    customer, _ = Customer.objects.update_or_create(
        store=store_domain(), shopify_id=node['id'], defaults={
            'first_name': node.get('firstName') or '', 'last_name': node.get('lastName') or '',
            'display_name': node.get('displayName') or '',
            'email': (node.get('defaultEmailAddress') or {}).get('emailAddress') or '',
            'phone': (node.get('defaultPhoneNumber') or {}).get('phoneNumber') or '',
            'last_order_id': order.get('id') or '', 'last_order_processed_at': processed_at,
            'last_order_amount': money.get('amount'), 'last_order_currency': money.get('currencyCode') or '',
            'fetched_at': timezone.now(),
        })
    return customer

def search_customers(term, after=None):
    require_connection()
    data = graphql(SEARCH_CUSTOMERS, {'query': search_expression(term), 'after': after})['customers']
    with transaction.atomic():
        customers = [cache_customer(node) for node in data['nodes']]
    return {'customers': [customer.as_api() for customer in customers], 'pageInfo': data['pageInfo']}

def refresh_customer(customer):
    require_connection()
    if customer.store != store_domain():
        raise ValueError('This customer belongs to a different Shopify store.')
    node = graphql(GET_CUSTOMER, {'id': customer.shopify_id})['customer']
    if not node:
        raise ValueError('This customer is no longer available in Shopify. Search again before selecting a customer.')
    return cache_customer(node)

@transaction.atomic
def attach_customer(bill_id, customer, actor):
    if customer.store != store_domain():
        raise ValueError('This customer belongs to a different Shopify store.')
    if not Bill.objects.filter(pk=bill_id, status='open').update(status='open'):
        raise ValueError('Only open bills can change customers.')
    # A crashed request can leave Sending behind. After the HTTP timeout has
    # safely elapsed, a freshly verified explicit selection resolves it.
    CustomerCreation.objects.filter(bill_id=bill_id, state='sending',
        updated_at__lt=timezone.now()-timedelta(minutes=2)).update(state='review', updated_at=timezone.now())
    if CustomerCreation.objects.filter(bill_id=bill_id, state='sending').exists():
        raise ValueError('Customer creation is in progress. Wait for it to finish.')
    Bill.objects.filter(pk=bill_id).update(shopify_customer=customer,
        first_name=customer.first_name, last_name=customer.last_name, email=customer.email, phone=customer.phone)
    CustomerCreation.objects.filter(bill_id=bill_id, state='review').update(state='resolved', error='', updated_at=timezone.now())
    Audit.objects.create(bill_id=bill_id, actor=actor, event='Shopify customer selected')

def create_customer_for_bill(bill_id, details, actor):
    require_connection()
    with transaction.atomic():
        if not Bill.objects.filter(pk=bill_id, status='open').update(status='open'):
            raise ValueError('Only open bills can create customers.')
        if Bill.objects.get(pk=bill_id).shopify_customer_id:
            raise ValueError('This bill already has a Shopify customer. Remove the selection before creating another.')
        attempt, _ = CustomerCreation.objects.get_or_create(bill_id=bill_id)
        if not CustomerCreation.objects.filter(pk=attempt.pk, state__in=['pending', 'rejected']).update(state='sending', error='', updated_at=timezone.now()):
            raise ValueError('This bill already has a customer creation attempt. Search Shopify and select the customer instead of creating again.')
    customer_input = {'firstName': details['first_name'], 'lastName': details.get('last_name', '')}
    if details.get('email'): customer_input['email'] = details['email']
    if details.get('phone'): customer_input['phone'] = details['phone']
    try:
        result = graphql(CREATE_CUSTOMER, {'input': customer_input})['customerCreate']
        if result.get('customer'):
            # Store Shopify identity before allowing another local action.
            with transaction.atomic():
                customer = cache_customer({**result['customer'], 'lastOrder': None})
                CustomerCreation.objects.filter(pk=attempt.pk).update(state='succeeded', error='', updated_at=timezone.now())
                attach_customer(bill_id, customer, actor)
            return customer
        if result.get('userErrors'):
            error = ' '.join(item['message'] for item in result['userErrors'])
            CustomerCreation.objects.filter(pk=attempt.pk).update(state='rejected', error=error, updated_at=timezone.now())
            raise CustomerRejected(error)
        raise RuntimeError('Customer creation returned no identity.')
    except CustomerRejected:
        raise
    except Exception as exc:
        error = 'Shopify may have created this customer, but confirmation was interrupted. Search by email or phone and select the matching customer. Do not create again.'
        CustomerCreation.objects.filter(pk=attempt.pk).update(state='review', error=error, updated_at=timezone.now())
        raise ValueError(error) from exc

class CustomerRejected(ValueError):
    pass
