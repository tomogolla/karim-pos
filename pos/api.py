"""JSON boundary consumed by the SvelteKit reception application."""
import json
from decimal import Decimal
from functools import wraps

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .customers import attach_customer, create_customer_for_bill, refresh_customer, search_customers, store_domain
from .forms import CustomerForm, LineForm, PaymentForm, ShopifyCustomerForm, StaffForm
from .models import Audit, Bill, Customer, CustomerCreation, Line, Product, Staff, SyncJob
from .services import complete_payment
from .shopify import pull_catalog, send_job


def api_login_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'Authentication required.'}, status=401)
        return view(request, *args, **kwargs)
    return wrapped


def body(request):
    try:
        return json.loads(request.body or b'{}')
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ValueError('The request body must be valid JSON.')


def errors(form):
    return {key: [str(message) for message in values] for key, values in form.errors.items()}


def money(value):
    return f'{Decimal(value):.2f}'


def line_json(line):
    return {
        'id': line.pk, 'title': line.title, 'quantity': line.quantity,
        'price': money(line.price), 'discount': money(line.discount), 'total': money(line.total),
        'staffId': line.staff_id, 'staffName': line.staff_name or (line.staff.name if line.staff else ''),
        'product': {'id': line.product_id, 'kind': line.product.kind, 'sku': line.product.sku},
    }


def bill_json(bill, detailed=False):
    data = {
        'id': bill.pk, 'name': bill.name, 'customer': bill.customer, 'status': bill.status,
        'firstName': bill.first_name, 'lastName': bill.last_name, 'email': bill.email,
        'phone': bill.phone, 'notes': bill.notes, 'currency': bill.currency,
        'subtotal': money(bill.subtotal), 'discount': money(bill.discount),
        'total': money(bill.total), 'totalPaid': money(bill.total_paid),
        'createdAt': bill.created_at.isoformat(), 'paidAt': bill.paid_at.isoformat() if bill.paid_at else None,
        'shopifyCustomer': bill.shopify_customer.as_api() if bill.shopify_customer_id else None,
    }
    if detailed:
        data['lines'] = [line_json(line) for line in bill.lines.select_related('product', 'staff')]
        try:
            payment = bill.payment
            data['payment'] = {'method': payment.method, 'amount': money(payment.amount),
                               'tendered': money(payment.tendered), 'change': money(payment.change),
                               'reference': payment.reference, 'createdAt': payment.created_at.isoformat()}
        except Bill.payment.RelatedObjectDoesNotExist:
            data['payment'] = None
        try:
            sync = bill.sync
            data['sync'] = {'id': sync.pk, 'state': sync.state, 'stateLabel': sync.get_state_display(),
                            'attempts': sync.attempts, 'error': sync.error, 'shopifyId': sync.shopify_id}
        except Bill.sync.RelatedObjectDoesNotExist:
            data['sync'] = None
    return data


def product_json(product):
    return {'id': product.pk, 'title': product.title, 'sku': product.sku,
            'category': product.category, 'kind': product.kind, 'price': money(product.price),
            'inventory': product.inventory, 'tracked': product.tracked, 'taxable': product.taxable,
            'active': product.active, 'demo': product.demo,
            'syncedAt': product.synced_at.isoformat() if product.synced_at else None}


def staff_json(person):
    return {'id': person.pk, 'name': person.name, 'role': person.role, 'active': person.active,
            'openLines': getattr(person, 'open_lines', 0), 'revenue': money(getattr(person, 'revenue', 0))}


@require_GET
@ensure_csrf_cookie
@never_cache
def session(request):
    return JsonResponse({
        'authenticated': request.user.is_authenticated,
        'user': ({'username': request.user.username,
                  'name': request.user.get_full_name() or request.user.username} if request.user.is_authenticated else None),
        'config': {'currency': settings.POS_CURRENCY,
                   'shopifyConfigured': bool(settings.SHOPIFY_STORE and settings.SHOPIFY_ACCESS_TOKEN),
                   'liveSync': settings.SHOPIFY_LIVE_SYNC},
    })


@require_POST
def login_api(request):
    try:
        data = body(request)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    user = authenticate(request, username=data.get('username', ''), password=data.get('password', ''))
    if not user:
        return JsonResponse({'error': 'The username or password is incorrect.'}, status=400)
    login(request, user)
    return JsonResponse({'ok': True})


@require_POST
@api_login_required
def logout_api(request):
    logout(request)
    return JsonResponse({'ok': True})


@require_GET
@api_login_required
def dashboard(request):
    today = Bill.objects.filter(status='paid', paid_at__date=timezone.localdate())
    recent = Bill.objects.select_related('shopify_customer').prefetch_related('lines')[:8]
    return JsonResponse({'revenue': money(today.aggregate(value=Sum('total_paid'))['value'] or 0),
                         'completed': today.count(), 'openBills': Bill.objects.filter(status='open').count(),
                         'pendingSync': SyncJob.objects.exclude(state='synced').count(),
                         'today': timezone.localdate().isoformat(),
                         'recent': [bill_json(item) for item in recent]})


@require_http_methods(['GET', 'POST'])
@api_login_required
def bills(request):
    if request.method == 'POST':
        bill = Bill.objects.create(created_by=request.user, currency=settings.POS_CURRENCY)
        Audit.objects.create(bill=bill, actor=request.user, event='Bill created')
        return JsonResponse(bill_json(bill, True), status=201)
    rows = Bill.objects.filter(status='open').select_related('shopify_customer').prefetch_related('lines__staff')
    query = request.GET.get('q', '').strip()
    if query:
        rows = rows.filter(Q(first_name__icontains=query) | Q(last_name__icontains=query) |
                           Q(email__icontains=query) | Q(phone__icontains=query))
    return JsonResponse({'bills': [bill_json(item, True) for item in rows]})


@require_http_methods(['GET', 'PATCH', 'DELETE'])
@api_login_required
def bill_detail(request, pk):
    bill = get_object_or_404(Bill.objects.select_related('shopify_customer'), pk=pk)
    if request.method == 'GET':
        return JsonResponse(bill_json(bill, True))
    if bill.status != 'open':
        return JsonResponse({'error': 'Completed bills cannot be changed.'}, status=409)
    if request.method == 'DELETE':
        bill.status = 'void'
        bill.save(update_fields=['status'])
        Audit.objects.create(bill=bill, actor=request.user, event='Open bill voided')
        return JsonResponse({'ok': True})
    try:
        data = body(request)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    form = CustomerForm(data, instance=bill)
    if not form.is_valid():
        return JsonResponse({'error': 'Check the sale details.', 'fields': errors(form)}, status=400)
    form.save()
    return JsonResponse(bill_json(Bill.objects.get(pk=pk), True))


@require_POST
@api_login_required
@transaction.atomic
def add_line(request, pk):
    bill = get_object_or_404(Bill, pk=pk)
    if bill.status != 'open':
        return JsonResponse({'error': 'Completed bills cannot be changed.'}, status=409)
    try:
        data = body(request)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    product = get_object_or_404(Product, pk=data.get('productId'), active=True)
    line = Line.objects.create(bill=bill, product=product, title=product.title, price=product.price)
    Audit.objects.create(bill=bill, actor=request.user, event='Item added')
    return JsonResponse(line_json(line), status=201)


@require_http_methods(['PATCH', 'DELETE'])
@api_login_required
@transaction.atomic
def line_detail(request, pk):
    line = get_object_or_404(Line.objects.select_related('bill', 'product', 'staff'), pk=pk)
    if line.bill.status != 'open':
        return JsonResponse({'error': 'Completed bills cannot be changed.'}, status=409)
    bill_id = line.bill_id
    if request.method == 'DELETE':
        line.delete()
        Audit.objects.create(bill_id=bill_id, actor=request.user, event='Item removed')
        return JsonResponse({'ok': True})
    try:
        data = body(request)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    form = LineForm({'quantity': data.get('quantity'), 'price': data.get('price'),
                     'discount': data.get('discount', 0), 'staff': data.get('staffId')}, instance=line)
    if not form.is_valid():
        return JsonResponse({'error': 'Check the line item.', 'fields': errors(form)}, status=400)
    line = form.save()
    Audit.objects.create(bill_id=bill_id, actor=request.user, event='Line item updated')
    return JsonResponse(line_json(line))


@require_POST
@api_login_required
def checkout(request, pk):
    bill = get_object_or_404(Bill, pk=pk)
    try:
        data = body(request)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    form = PaymentForm(data)
    if not form.is_valid():
        return JsonResponse({'error': 'Check the payment details.', 'fields': errors(form)}, status=400)
    try:
        bill = complete_payment(pk, form.cleaned_data, request.user)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=409)
    return JsonResponse(bill_json(Bill.objects.get(pk=bill.pk), True))


@require_GET
@api_login_required
def receipt(request, pk):
    return JsonResponse(bill_json(get_object_or_404(Bill.objects.select_related('shopify_customer'), pk=pk), True))


@require_http_methods(['GET', 'POST'])
@api_login_required
def catalog(request):
    if request.method == 'POST':
        try:
            count = pull_catalog()
            return JsonResponse({'ok': True, 'count': count})
        except ValueError as exc:
            return JsonResponse({'error': str(exc)}, status=400)
        except Exception:
            return JsonResponse({'error': 'Catalog import failed. Check the connection and permissions; the previous catalog is preserved.'}, status=503)
    rows = Product.objects.all()
    query = request.GET.get('q', '').strip()
    category = request.GET.get('category', '').strip()
    active = request.GET.get('active')
    if query:
        rows = rows.filter(Q(title__icontains=query) | Q(sku__icontains=query))
    if category:
        rows = rows.filter(category=category)
    if active == '1':
        rows = rows.filter(active=True)
    categories = list(Product.objects.filter(active=True).order_by('category').values_list('category', flat=True).distinct())
    return JsonResponse({'products': [product_json(item) for item in rows], 'categories': categories})


@require_http_methods(['GET', 'POST'])
@api_login_required
def staff(request):
    if request.method == 'POST':
        try:
            data = body(request)
        except ValueError as exc:
            return JsonResponse({'error': str(exc)}, status=400)
        form = StaffForm(data)
        if not form.is_valid():
            return JsonResponse({'error': 'Check the staff details.', 'fields': errors(form)}, status=400)
        return JsonResponse(staff_json(form.save()), status=201)
    people = list(Staff.objects.all())
    today = timezone.localdate()
    for person in people:
        person.open_lines = person.line_set.filter(bill__status='open').count()
        person.revenue = sum((item.total for item in person.line_set.filter(
            bill__status='paid', bill__paid_at__date=today)), Decimal('0'))
    return JsonResponse({'staff': [staff_json(person) for person in people]})


@require_GET
@api_login_required
def orders(request):
    rows = SyncJob.objects.select_related('bill').order_by('-pk')
    state = request.GET.get('state', '').strip()
    if state:
        rows = rows.filter(state=state)
    return JsonResponse({'liveSync': settings.SHOPIFY_LIVE_SYNC, 'orders': [
        {'id': job.pk, 'state': job.state, 'stateLabel': job.get_state_display(),
         'attempts': job.attempts, 'error': job.error, 'shopifyId': job.shopify_id,
         'updatedAt': job.updated_at.isoformat(), 'bill': bill_json(job.bill)} for job in rows]})


@require_POST
@api_login_required
def retry_order(request, pk):
    get_object_or_404(SyncJob, pk=pk)
    try:
        result = send_job(pk)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    job = SyncJob.objects.select_related('bill').get(pk=pk)
    return JsonResponse({'ok': result, 'state': job.state, 'error': job.error})


@require_GET
@api_login_required
def order_payload(request, pk):
    job = get_object_or_404(SyncJob, pk=pk)
    return JsonResponse({'id': job.pk, 'bill': job.bill.name, 'payload': job.payload})


@require_GET
@api_login_required
def reports(request):
    rows = Bill.objects.filter(status='paid', paid_at__date=timezone.localdate()).prefetch_related('lines__product')
    categories = {}
    for sale in rows:
        for item in sale.lines.all():
            categories[item.product.category] = categories.get(item.product.category, Decimal('0')) + item.total
    events = Audit.objects.select_related('actor', 'bill').order_by('-pk')[:20]
    return JsonResponse({'revenue': money(sum((item.total_paid for item in rows), Decimal('0'))),
                         'completed': rows.count(), 'pendingSync': SyncJob.objects.exclude(state='synced').count(),
                         'categories': [{'name': name, 'amount': money(amount)} for name, amount in categories.items()],
                         'events': [{'id': event.pk, 'event': event.event,
                                     'bill': event.bill.name if event.bill else '',
                                     'actor': event.actor.get_full_name() or event.actor.username if event.actor else '',
                                     'createdAt': event.created_at.isoformat()} for event in events]})


@require_GET
@api_login_required
def settings_api(request):
    return JsonResponse({'store': settings.SHOPIFY_STORE, 'apiVersion': settings.SHOPIFY_API_VERSION,
                         'currency': settings.POS_CURRENCY, 'liveSync': settings.SHOPIFY_LIVE_SYNC,
                         'shopifyConfigured': bool(settings.SHOPIFY_STORE and settings.SHOPIFY_ACCESS_TOKEN),
                         'inventoryBehaviour': settings.SHOPIFY_INVENTORY_BEHAVIOUR})


@require_POST
@api_login_required
def select_customer(request, pk):
    bill = get_object_or_404(Bill, pk=pk, status='open')
    try:
        customer_id = body(request).get('customerId')
        customer = get_object_or_404(Customer, pk=customer_id, store=store_domain())
        customer = refresh_customer(customer)
        attach_customer(bill.pk, customer, request.user)
        return JsonResponse(bill_json(Bill.objects.get(pk=pk), True))
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    except Exception:
        return JsonResponse({'error': 'Could not confirm this customer with Shopify.'}, status=503)


@require_http_methods(['POST', 'DELETE'])
@api_login_required
def bill_customer(request, pk):
    bill = get_object_or_404(Bill, pk=pk, status='open')
    if request.method == 'DELETE':
        if CustomerCreation.objects.filter(bill_id=pk, state__in=['sending', 'review']).exists():
            return JsonResponse({'error': 'Resolve the current customer creation attempt first.'}, status=409)
        Bill.objects.filter(pk=pk).update(shopify_customer=None, first_name='Walk-in', last_name='', email='', phone='')
        Audit.objects.create(bill_id=pk, actor=request.user, event='Shopify customer selection removed')
        return JsonResponse(bill_json(Bill.objects.get(pk=pk), True))
    try:
        data = body(request)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    form = ShopifyCustomerForm(data)
    if not form.is_valid():
        return JsonResponse({'error': 'Check the customer details.', 'fields': errors(form)}, status=400)
    try:
        create_customer_for_bill(pk, form.cleaned_data, request.user)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    return JsonResponse(bill_json(Bill.objects.get(pk=pk), True), status=201)
