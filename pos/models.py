import uuid
from decimal import Decimal
from django.conf import settings
from django.db import models
from django.db.models import Q, F

class Staff(models.Model):
    name = models.CharField(max_length=120)
    role = models.CharField(max_length=80, default='Stylist')
    active = models.BooleanField(default=True)
    class Meta:
        ordering = ['name']
    def __str__(self): return self.name

class Product(models.Model):
    variant_id = models.CharField(max_length=100, unique=True)
    title = models.CharField(max_length=255)
    sku = models.CharField(max_length=100, blank=True)
    category = models.CharField(max_length=100, default='Retail')
    kind = models.CharField(max_length=10, choices=[('service', 'Service'), ('retail', 'Retail')])
    price = models.DecimalField(max_digits=12, decimal_places=2)
    inventory = models.IntegerField(null=True, blank=True)
    tracked = models.BooleanField(default=False)
    taxable = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    demo = models.BooleanField(default=False)
    synced_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        ordering = ['title']
        constraints = [models.CheckConstraint(condition=Q(price__gte=0), name='product_nonnegative_price')]
    def __str__(self): return self.title

class Customer(models.Model):
    """Store-scoped cache; bills retain their own customer contact snapshots."""
    store = models.CharField(max_length=255)
    shopify_id = models.CharField(max_length=100)
    first_name = models.CharField(max_length=255, blank=True)
    last_name = models.CharField(max_length=255, blank=True)
    display_name = models.CharField(max_length=600, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    last_order_id = models.CharField(max_length=100, blank=True)
    last_order_processed_at = models.DateTimeField(null=True, blank=True)
    last_order_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    last_order_currency = models.CharField(max_length=3, blank=True)
    fetched_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['store', 'shopify_id'], name='customer_per_shopify_store')]
        ordering = ['display_name', 'pk']

    def __str__(self):
        return self.display_name or self.email or self.shopify_id

    def as_api(self):
        last_order = None
        if self.last_order_id:
            last_order = {
                'id': self.last_order_id,
                'processedAt': self.last_order_processed_at.isoformat() if self.last_order_processed_at else None,
                'currentTotalPriceSet': {'shopMoney': {
                    'amount': str(self.last_order_amount) if self.last_order_amount is not None else None,
                    'currencyCode': self.last_order_currency,
                }},
            }
        return {'localId': self.pk, 'id': self.shopify_id, 'displayName': str(self),
                'firstName': self.first_name, 'lastName': self.last_name,
                'email': self.email, 'phone': self.phone, 'lastOrder': last_order,
                'fetchedAt': self.fetched_at.isoformat()}

class Bill(models.Model):
    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    shopify_customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.PROTECT, related_name='bills')
    first_name = models.CharField(max_length=255, default='Walk-in')
    last_name = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=12, default='open', choices=[('open', 'Open'), ('paid', 'Paid'), ('void', 'Void')])
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    currency = models.CharField(max_length=3, default='KES')
    total_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    class Meta:
        ordering = ['-created_at']
    @property
    def name(self): return f'#KP-{self.pk + 1000}'
    @property
    def customer(self): return f'{self.first_name} {self.last_name}'.strip() or self.email or self.phone or 'Customer'
    @property
    def subtotal(self): return sum((l.price*l.quantity for l in self.lines.all()), Decimal('0.00'))
    @property
    def discount(self): return sum((l.discount*l.quantity for l in self.lines.all()), Decimal('0.00'))
    @property
    def total(self): return self.subtotal - self.discount

class Line(models.Model):
    bill = models.ForeignKey(Bill, related_name='lines', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    title = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    staff = models.ForeignKey(Staff, null=True, blank=True, on_delete=models.PROTECT)
    staff_name = models.CharField(max_length=120, blank=True)
    class Meta:
        ordering = ['pk']
        constraints = [models.CheckConstraint(condition=Q(quantity__gte=1, price__gte=0, discount__gte=0, discount__lte=F('price')), name='valid_line_amounts')]
    @property
    def total(self): return (self.price-self.discount)*self.quantity

class Payment(models.Model):
    bill = models.OneToOneField(Bill, on_delete=models.PROTECT)
    method = models.CharField(max_length=10, choices=[('cash', 'Cash'), ('mpesa', 'M-Pesa'), ('card', 'Card')])
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    tendered = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=120, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    @property
    def change(self): return self.tendered-self.amount

class SyncJob(models.Model):
    bill = models.OneToOneField(Bill, related_name='sync', on_delete=models.PROTECT)
    state = models.CharField(max_length=16, default='pending', choices=[('pending','Pending'),('sending','Sending'),('synced','Synced'),('failed','Failed'),('review','Needs review')])
    attempts = models.PositiveIntegerField(default=0)
    shopify_id = models.CharField(max_length=100, blank=True)
    error = models.TextField(blank=True)
    payload = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

class CustomerCreation(models.Model):
    """One durable create attempt per bill; uncertain results require a customer selection."""
    bill = models.OneToOneField(Bill, related_name='customer_creation', on_delete=models.PROTECT)
    state = models.CharField(max_length=16, default='pending', choices=[
        ('pending', 'Pending'), ('sending', 'Sending'), ('succeeded', 'Created'),
        ('rejected', 'Rejected'), ('review', 'Needs review'), ('resolved', 'Customer selected')])
    error = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

class Audit(models.Model):
    bill = models.ForeignKey(Bill, on_delete=models.PROTECT, null=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    event = models.CharField(max_length=180)
    created_at = models.DateTimeField(auto_now_add=True)
