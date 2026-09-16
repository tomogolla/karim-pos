from django.db import transaction
from django.utils import timezone
from .models import Bill, Payment, SyncJob, Audit, CustomerCreation
from .shopify import build_payload

@transaction.atomic
def complete_payment(bill_id, data, actor):
    # Conditional write also serializes SQLite writers, where select_for_update is a no-op.
    if not Bill.objects.filter(pk=bill_id, status='open').update(status='paid'):
        raise ValueError('This bill is already completed or voided.')
    bill = Bill.objects.get(pk=bill_id)
    if CustomerCreation.objects.filter(bill_id=bill_id, state__in=['sending', 'review']).exists():
        raise ValueError('Resolve the Shopify customer creation attempt by selecting the customer before checkout.')
    lines = list(bill.lines.select_related('staff', 'product'))
    if not lines: raise ValueError('Add at least one item before checkout.')
    if any(not line.staff or not line.staff.active for line in lines): raise ValueError('Assign an active staff member to every line item.')
    if any(not line.product.active for line in lines): raise ValueError('A product is no longer active. Remove it before checkout.')
    total = bill.total
    if total <= 0: raise ValueError('The total must be greater than zero.')
    if data['tendered'] < total: raise ValueError('Amount received is less than the bill total.')
    if data['method'] != 'cash' and data['tendered'] != total: raise ValueError('M-Pesa and card amounts must equal the bill total.')
    for line in lines:
        line.staff_name = line.staff.name
        line.save(update_fields=['staff_name'])
    bill.paid_at, bill.total_paid = timezone.now(), total
    bill.save(update_fields=['paid_at', 'total_paid'])
    Payment.objects.create(bill=bill, method=data['method'], amount=total, tendered=data['tendered'], reference=data['reference'], recorded_by=actor)
    SyncJob.objects.create(bill=bill, payload=build_payload(bill))
    Audit.objects.create(bill=bill, actor=actor, event='Payment manually confirmed; order queued')
    return bill
