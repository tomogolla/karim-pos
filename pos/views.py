import csv
import json
from decimal import Decimal
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.cache import never_cache
from .models import Bill, Line, Staff, Product, SyncJob, Audit, Customer, CustomerCreation
from .forms import CustomerForm, LineForm, PaymentForm, StaffForm, ShopifyCustomerForm
from .customers import search_customers, refresh_customer, create_customer_for_bill, attach_customer, store_domain
from .services import complete_payment
from .shopify import pull_catalog, send_job

def page(request, template, title, subtitle='', **context):
    return render(request, f'pos/{template}.html', {'title': title, 'subtitle': subtitle, **context})

@login_required
def dashboard(request):
    today = Bill.objects.filter(status='paid', paid_at__date=timezone.localdate())
    return page(request, 'dashboard', 'Receptionist desk', 'A clear view of your salon, today.',
        revenue=today.aggregate(value=Sum('total_paid'))['value'] or 0,
        count=today.count(), open_count=Bill.objects.filter(status='open').count(),
        pending=SyncJob.objects.exclude(state='synced').count(),
        bills=Bill.objects.select_related('sync').prefetch_related('lines')[:8], today=timezone.localdate())

@login_required
@require_POST
def new_bill(request):
    bill = Bill.objects.create(created_by=request.user, currency=settings.POS_CURRENCY)
    Audit.objects.create(bill=bill, actor=request.user, event='Bill created')
    return redirect('bill', pk=bill.pk)

@login_required
def bill(request, pk):
    current = get_object_or_404(Bill, pk=pk)
    if current.status != 'open': return redirect('receipt', pk=pk)
    form = CustomerForm(instance=current)
    new_customer_form = ShopifyCustomerForm(prefix='new')
    if request.method == 'POST' and request.POST.get('action') == 'create_shopify_customer':
        new_customer_form = ShopifyCustomerForm(request.POST, prefix='new')
        if new_customer_form.is_valid():
            try:
                create_customer_for_bill(pk, new_customer_form.cleaned_data, request.user)
                messages.success(request, 'Customer created in Shopify and selected for this sale.')
                return redirect('bill', pk=pk)
            except ValueError as exc:
                new_customer_form.add_error(None, str(exc))
    elif request.method == 'POST':
        with transaction.atomic():
            if not Bill.objects.filter(pk=pk, status='open').update(status='open'):
                messages.error(request, 'This bill is no longer open.')
                return redirect('receipt', pk=pk)
            form = CustomerForm(request.POST, instance=Bill.objects.get(pk=pk))
            if form.is_valid():
                form.save()
                messages.success(request, 'Sale details saved.')
                return redirect('bill', pk=pk)
    products = Product.objects.filter(active=True)
    query = request.GET.get('q','')
    category = request.GET.get('category','')
    if query: products = products.filter(Q(title__icontains=query) | Q(sku__icontains=query))
    if category: products = products.filter(category=category)
    return page(request, 'bill', 'Create a sale', f'{current.name} · Select items, assign staff, then checkout.', bill=current,
        form=form, products=products, categories=Product.objects.filter(active=True).order_by('category').values_list('category',flat=True).distinct(),
        query=query, category=category, new_customer_form=new_customer_form,
        customer_creation=CustomerCreation.objects.filter(bill_id=pk).first(),
        line_forms=[(line, LineForm(instance=line)) for line in current.lines.select_related('product','staff')])

@login_required
@require_GET
@never_cache
def customer_search_api(request):
    term = request.GET.get('q', '').strip()
    after = request.GET.get('after') or None
    if not 2 <= len(term) <= 120 or (after and len(after) > 2048):
        return JsonResponse({'error': 'Enter between 2 and 120 characters to search.'}, status=400)
    try:
        return JsonResponse(search_customers(term, after))
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=503)
    except Exception:
        return JsonResponse({'error': 'Customer search is unavailable. Check the Shopify connection and customer/order access permissions. Your bill is unchanged.'}, status=503)

@login_required
@require_GET
@never_cache
def customer_detail_api(request, pk):
    customer = get_object_or_404(Customer, pk=pk, store=store_domain())
    return JsonResponse(customer.as_api())

@login_required
@require_POST
@never_cache
def customer_refresh_api(request, pk):
    customer = get_object_or_404(Customer, pk=pk, store=store_domain())
    try:
        return JsonResponse(refresh_customer(customer).as_api())
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=503)
    except Exception:
        return JsonResponse({'error': 'Unable to refresh Shopify customer history. The previous cache is preserved.'}, status=503)

@login_required
@require_POST
def select_customer(request, pk):
    bill = get_object_or_404(Bill, pk=pk)
    if bill.status != 'open':
        messages.error(request, 'Completed bills cannot change customers.')
        return redirect('receipt', pk=pk)
    customer_id = request.POST.get('customer_id', '')
    if not customer_id.isdigit():
        messages.error(request, 'Choose a customer from the search results.')
        return redirect('bill', pk=pk)
    customer = get_object_or_404(Customer, pk=customer_id, store=store_domain())
    try:
        customer = refresh_customer(customer)
        attach_customer(pk, customer, request.user)
        messages.success(request, 'Shopify customer selected. Latest order details refreshed.')
    except ValueError as exc:
        messages.error(request, str(exc))
    except Exception:
        messages.error(request, 'Could not confirm this customer with Shopify. Try again when the connection is available.')
    return redirect('bill', pk=pk)

@login_required
@require_POST
@transaction.atomic
def clear_customer(request, pk):
    if not Bill.objects.filter(pk=pk, status='open').update(status='open'):
        messages.error(request, 'Only open bills can change customers.')
        return redirect('receipt', pk=pk)
    if CustomerCreation.objects.filter(bill_id=pk, state__in=['sending', 'review']).exists():
        messages.error(request, 'Resolve the current customer creation attempt first.')
        return redirect('bill', pk=pk)
    Bill.objects.filter(pk=pk).update(shopify_customer=None, first_name='Walk-in', last_name='', email='', phone='')
    Audit.objects.create(bill_id=pk, actor=request.user, event='Shopify customer selection removed')
    messages.success(request, 'Customer selection removed. The customer remains in Shopify.')
    return redirect('bill', pk=pk)

@login_required
@require_POST
@transaction.atomic
def add_line(request, pk):
    current = get_object_or_404(Bill, pk=pk)
    if not Bill.objects.filter(pk=pk, status='open').update(status='open'):
        messages.error(request,'Completed bills cannot be changed.')
        return redirect('receipt', pk=pk)
    product = get_object_or_404(Product, pk=request.POST.get('product'), active=True)
    Line.objects.create(bill=current, product=product, title=product.title, price=product.price)
    Audit.objects.create(bill=current, actor=request.user, event='Item added')
    return redirect('bill', pk=pk)

@login_required
@require_POST
@transaction.atomic
def update_line(request, pk):
    line = get_object_or_404(Line, pk=pk)
    if not Bill.objects.filter(pk=line.bill_id, status='open').update(status='open'):
        messages.error(request, 'Completed bills cannot be changed.')
        return redirect('receipt', pk=line.bill_id)
    if request.POST.get('action') == 'remove': line.delete()
    else:
        form = LineForm(request.POST, instance=line)
        if form.is_valid(): form.save()
        else: messages.error(request, ' '.join(f'{key}: {", ".join(value)}' for key,value in form.errors.items()))
    Audit.objects.create(bill_id=line.bill_id, actor=request.user, event='Line item updated')
    return redirect('bill', pk=line.bill_id)

@login_required
def checkout(request, pk):
    current = get_object_or_404(Bill, pk=pk)
    if current.status != 'open': return redirect('receipt', pk=pk)
    form = PaymentForm(request.POST or None, initial={'tendered':current.total})
    if request.method == 'POST' and form.is_valid():
        try:
            complete_payment(pk, form.cleaned_data, request.user)
            messages.success(request, 'Payment recorded. Your order is saved and queued for Shopify.')
            return redirect('receipt', pk=pk)
        except ValueError as exc: form.add_error(None, str(exc))
    return page(request, 'checkout', 'Payments & checkout', 'Confirm payment received and complete this sale.', bill=current, form=form)

@login_required
def receipt(request, pk):
    current = get_object_or_404(Bill, pk=pk)
    return page(request, 'receipt', current.name, 'Sale details and receipt', bill=current)

@login_required
def bills(request):
    rows = Bill.objects.filter(status='open').prefetch_related('lines__staff')
    q = request.GET.get('q','')
    if q: rows = rows.filter(Q(first_name__icontains=q)|Q(last_name__icontains=q))
    return page(request,'bills','Open bills','Resume a saved bill when your customer is ready.', bills=rows, query=q)

@login_required
@require_POST
def void_bill(request, pk):
    if Bill.objects.filter(pk=pk,status='open').update(status='void'):
        Audit.objects.create(bill_id=pk,actor=request.user,event='Open bill voided')
        messages.success(request,'Bill voided.')
    else: messages.error(request,'Only open bills can be voided.')
    return redirect('bills')

@login_required
def orders(request):
    rows = SyncJob.objects.select_related('bill').order_by('-pk')
    state = request.GET.get('state','')
    if state: rows = rows.filter(state=state)
    return page(request,'orders','Orders & Shopify sync','Completed sales, delivery status, and sync history.', jobs=rows, state=state)

@login_required
@require_POST
def retry(request, pk):
    get_object_or_404(SyncJob, pk=pk)
    try:
        result = send_job(pk)
        messages.info(request,'Order synchronized.' if result else 'See the order status for the result. No unsafe retry was performed.')
    except ValueError as exc: messages.error(request,str(exc))
    return redirect('orders')

@login_required
def payload(request, pk):
    job = get_object_or_404(SyncJob, pk=pk)
    return page(request,'payload','Shopify order payload',job.bill.name,payload=json.dumps(job.payload,indent=2))

@login_required
def catalog(request):
    q = request.GET.get('q','')
    rows = Product.objects.all()
    if q: rows = rows.filter(Q(title__icontains=q)|Q(sku__icontains=q))
    return page(request,'catalog','Services & catalog','Services and retail products from your Shopify catalog.',products=rows,query=q)

@login_required
@require_POST
def import_catalog(request):
    try:
        count = pull_catalog()
        messages.success(request,f'{count} variants imported from Shopify.')
    except ValueError as exc: messages.error(request,str(exc))
    except Exception: messages.error(request,'Catalog import failed. Check the connection and app permissions; your previous catalog is preserved.')
    return redirect('catalog')

@login_required
def staff(request):
    form = StaffForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request,'Staff member added.')
        return redirect('staff')
    people = Staff.objects.all()
    for person in people:
        person.open_lines = person.line_set.filter(bill__status='open').count()
        person.revenue = sum((l.total for l in person.line_set.filter(bill__status='paid',bill__paid_at__date=timezone.localdate())), Decimal('0'))
    return page(request,'staff','Staff directory','Staff assignments and today’s attributed sales.',people=people,form=form)

@login_required
def reports(request):
    rows = Bill.objects.filter(status='paid',paid_at__date=timezone.localdate()).prefetch_related('lines__product')
    categories = {}
    for sale in rows:
        for line in sale.lines.all(): categories[line.product.category] = categories.get(line.product.category,0)+line.total
    return page(request,'reports','Daily operations report','Today’s completed sales and staff attribution. Discounts are deducted; no tax is calculated in this first version.',
                revenue=sum((b.total_paid for b in rows),Decimal('0')),count=rows.count(),categories=categories,
                pending=SyncJob.objects.exclude(state='synced').count(),events=Audit.objects.select_related('actor','bill').order_by('-pk')[:20])

@login_required
def export(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="karim-sales.csv"'
    writer = csv.writer(response)
    writer.writerow(['Order','Created','Customer','Currency','Item','Staff','Quantity','Price','Discount per item','Line total','Payment amount'])
    def safe(value):
        value = str(value)
        return "'"+value if value.startswith(('=','+','-','@','\t','\r','\n')) else value
    for line in Line.objects.filter(bill__status='paid').select_related('bill'):
        b=line.bill
        writer.writerow([safe(v) for v in [b.name,b.created_at.isoformat(),b.customer,b.currency,line.title,line.staff_name,line.quantity,line.price,line.discount,line.total,b.total_paid]])
    return response

@login_required
def settings_page(request):
    return page(request,'settings','Settings','Connection status and local configuration.',store=settings.SHOPIFY_STORE,version=settings.SHOPIFY_API_VERSION)
