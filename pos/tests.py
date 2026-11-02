from decimal import Decimal
from io import StringIO
from unittest.mock import patch
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Bill, Line, Product, Staff, Payment, SyncJob
from .services import complete_payment
from .shopify import send_job, pull_catalog

class SaleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('reception','test@example.com','test-password-123')
        self.client.force_login(self.user)
        self.staff = Staff.objects.create(name='Kelvin',role='Stylist')
        self.product = Product.objects.create(variant_id='gid://shopify/ProductVariant/1',title='Haircut',price='1500',kind='service')
        self.bill = Bill.objects.create(created_by=self.user,first_name='Jane')
        self.line = Line.objects.create(bill=self.bill,product=self.product,title='Haircut',price='1500',quantity=2,discount='100',staff=self.staff)
        self.data={'method':'cash','tendered':Decimal('3000'),'reference':'','confirmed':True}
    def pay(self): return complete_payment(self.bill.pk,self.data,self.user)
    def test_amounts_staff_and_payload_snapshot(self):
        bill=self.pay()
        self.assertEqual(bill.total_paid,Decimal('2800'))
        self.assertEqual(bill.payment.change,Decimal('200'))
        payload=bill.sync.payload
        self.assertEqual(payload['order']['lineItems'][0]['priceSet']['shopMoney']['amount'],'1400.00')
        props={p['name']:p['value'] for p in payload['order']['lineItems'][0]['properties']}
        self.assertEqual(props['Staff'],'Kelvin')
        self.assertEqual(props['Discount per item'],'100.00')
        self.assertEqual(props['Line total'],'2800.00')
        self.assertEqual(payload['order']['tags'],['karim-pos'])
        self.assertEqual(payload['order']['sourceIdentifier'],str(bill.uid))
        self.staff.name='Changed';self.staff.save()
        self.assertEqual(Bill.objects.get(pk=bill.pk).sync.payload,payload)
    def test_double_checkout_creates_one_payment_and_one_job(self):
        self.pay()
        with self.assertRaises(ValueError): self.pay()
        self.assertEqual(Payment.objects.count(),1)
        self.assertEqual(SyncJob.objects.count(),1)
    def test_missing_staff_rolls_back_payment_and_bill_status(self):
        self.line.staff=None;self.line.save()
        with self.assertRaises(ValueError): self.pay()
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status,'open')
        self.assertFalse(Payment.objects.exists())
    def test_underpayment_and_card_overpayment_rejected(self):
        self.data['tendered']=Decimal('1')
        with self.assertRaises(ValueError): self.pay()
        self.data.update(method='card',tendered=Decimal('3000'),reference='REF')
        with self.assertRaises(ValueError): self.pay()
        self.assertFalse(SyncJob.objects.exists())
    def test_discount_larger_than_price_rejected(self):
        response=self.client.post(reverse('update_line',args=[self.line.pk]),{'quantity':2,'price':'1500','discount':'1600','staff':self.staff.pk})
        self.assertEqual(response.status_code,302)
        self.line.refresh_from_db(); self.assertEqual(self.line.discount,Decimal('100'))
    def test_zero_quantity_rejected(self):
        self.client.post(reverse('update_line',args=[self.line.pk]),{'quantity':0,'price':'1500','discount':'0','staff':self.staff.pk})
        self.line.refresh_from_db();self.assertEqual(self.line.quantity,2)
    def test_completed_bill_cannot_be_edited(self):
        self.pay()
        self.client.post(reverse('update_line',args=[self.line.pk]),{'action':'remove'})
        self.client.post(reverse('add_line',args=[self.bill.pk]),{'product':self.product.pk})
        self.client.post(reverse('bill',args=[self.bill.pk]),{'first_name':'Other'})
        self.assertEqual(self.bill.lines.count(),1)
        self.bill.refresh_from_db();self.assertEqual(self.bill.first_name,'Jane')
    def test_payment_reference_and_confirmation_required(self):
        response=self.client.post(reverse('checkout',args=[self.bill.pk]),{'method':'mpesa','tendered':'2800','confirmed':'on'})
        self.assertContains(response,'payment reference is required')
        self.assertFalse(Payment.objects.exists())
        self.client.post(reverse('checkout',args=[self.bill.pk]),{'method':'cash','tendered':'2800'})
        self.assertFalse(Payment.objects.exists())
    def test_pages_render_with_open_and_paid_orders(self):
        for name in ['dashboard','bills','catalog','staff','orders','reports','settings']:
            self.assertEqual(self.client.get(reverse(name)).status_code,200,name)
        for name in ['bill','checkout','receipt']:
            self.assertEqual(self.client.get(reverse(name,args=[self.bill.pk])).status_code,200,name)
        self.pay()
        for name in ['dashboard','orders','reports','staff','export']:
            self.assertEqual(self.client.get(reverse(name)).status_code,200,name)
        self.assertEqual(self.client.get(reverse('payload',args=[self.bill.sync.pk])).status_code,200)
        self.assertEqual(self.client.get(reverse('receipt',args=[self.bill.pk])).status_code,200)
    def test_unassigned_bill_page_renders(self):
        self.line.staff=None;self.line.save()
        for name in ['bill','checkout','receipt']:
            self.assertEqual(self.client.get(reverse(name,args=[self.bill.pk])).status_code,200)
    def test_login_and_post_required(self):
        self.client.logout()
        self.assertEqual(self.client.get('/').status_code,200)
        self.assertEqual(self.client.post(reverse('new_bill')).status_code,302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('new_bill')).status_code,405)
    def test_demo_login_routes_removed(self):
        self.client.logout()
        self.assertEqual(self.client.post('/demo-login/').status_code,404)
        self.assertEqual(self.client.post('/api/session/demo/').status_code,404)
        self.assertNotIn('demoMode',self.client.get('/api/session/').json()['config'])
    def test_seed_demo_requires_debug(self):
        with self.assertRaises(CommandError): call_command('seed_demo',stdout=StringIO())
        self.assertFalse(Product.objects.filter(demo=True).exists())
        with override_settings(DEBUG=True): call_command('seed_demo',stdout=StringIO())
        self.assertEqual(Product.objects.filter(demo=True).count(),8)
    @override_settings(SHOPIFY_LIVE_SYNC=True,SHOPIFY_ACCESS_TOKEN='test-token')
    @patch('pos.shopify.graphql')
    def test_sync_sends_once(self,api):
        api.return_value={'orderCreate':{'order':{'id':'gid://shopify/Order/123'},'userErrors':[]}}
        job=self.pay().sync
        self.assertTrue(send_job(job.pk));self.assertFalse(send_job(job.pk))
        self.assertEqual(api.call_count,1)
    @override_settings(SHOPIFY_LIVE_SYNC=True,SHOPIFY_ACCESS_TOKEN='test-token')
    @patch('pos.shopify.graphql')
    def test_failed_retry_rebuilds_corrected_payload(self,api):
        job=self.pay().sync
        stale_payload=job.payload
        stale_payload['order']['tags']=['karim-pos',f'karim-{job.bill.uid}']
        job.payload=stale_payload
        job.state='failed'
        job.error='Order tags is invalid'
        job.save(update_fields=['payload','state','error'])
        api.return_value={'orderCreate':{'order':{'id':'gid://shopify/Order/124'},'userErrors':[]}}

        self.assertTrue(send_job(job.pk))

        sent_payload=api.call_args.args[1]
        self.assertEqual(sent_payload['order']['tags'],['karim-pos'])
        self.assertEqual(sent_payload['order']['sourceIdentifier'],str(job.bill.uid))
        job.refresh_from_db()
        self.assertEqual(job.payload,sent_payload)
        self.assertEqual(job.state,'synced')
    @override_settings(SHOPIFY_LIVE_SYNC=True,SHOPIFY_ACCESS_TOKEN='test-token')
    @patch('pos.shopify.graphql',side_effect=TimeoutError)
    def test_ambiguous_sync_is_not_retried(self,api):
        job=self.pay().sync
        self.assertFalse(send_job(job.pk));self.assertFalse(send_job(job.pk))
        job.refresh_from_db();self.assertEqual(job.state,'review')
        self.assertEqual(api.call_count,1)
    @override_settings(SHOPIFY_LIVE_SYNC=True,SHOPIFY_ACCESS_TOKEN='test-token')
    @patch('pos.shopify.graphql')
    def test_demo_orders_never_sent(self,api):
        self.product.demo=True;self.product.save()
        with self.assertRaises(ValueError):send_job(self.pay().sync.pk)
        api.assert_not_called()
    @patch('pos.shopify.graphql')
    def test_catalog_pagination_and_archive(self,api):
        def node(i): return {'id':f'gid://shopify/ProductVariant/{i}','title':'Default Title','sku':f'X{i}','price':'120','inventoryQuantity':10,'taxable':True,'inventoryItem':{'tracked':False,'requiresShipping':False},'product':{'title':'Service','status':'ACTIVE','productType':'Hair','tags':['service']}}
        api.side_effect=[{'shop':{'currencyCode':'KES'},'productVariants':{'nodes':[node(2)],'pageInfo':{'hasNextPage':True,'endCursor':'cursor'}}},{'shop':{'currencyCode':'KES'},'productVariants':{'nodes':[node(3)],'pageInfo':{'hasNextPage':False,'endCursor':'last'}}}]
        self.assertEqual(pull_catalog(),2)
        self.product.refresh_from_db();self.assertFalse(self.product.active)
        self.assertEqual(Product.objects.filter(active=True,kind='service').count(),2)
        self.assertEqual(api.call_args.args[1],{'after':'cursor'})
    @patch('pos.shopify.graphql',side_effect=TimeoutError)
    def test_failed_import_preserves_catalog(self,api):
        with self.assertRaises(TimeoutError):pull_catalog()
        self.product.refresh_from_db();self.assertTrue(self.product.active)
    def test_csv_formula_injection_escaped(self):
        self.line.title='=1+1';self.line.save();self.pay()
        self.assertContains(self.client.get(reverse('export')), "'=1+1")
