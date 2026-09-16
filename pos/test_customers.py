from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase, override_settings, Client
from django.urls import reverse
from django.utils import timezone
from .customers import (cache_customer, search_customers, search_expression,
                        refresh_customer, attach_customer, create_customer_for_bill)
from .models import Bill, Customer, CustomerCreation, Line, Product, Staff, SyncJob
from .services import complete_payment

def shopify_customer(customer_id='123'):
    return {
        'id': f'gid://shopify/Customer/{customer_id}', 'firstName': 'Jane', 'lastName': 'Doe',
        'displayName': 'Jane Doe', 'defaultEmailAddress': {'emailAddress': 'jane@example.com'},
        'defaultPhoneNumber': {'phoneNumber': '+254712345678'},
        'lastOrder': {'id': 'gid://shopify/Order/6513859199159', 'processedAt': '2026-09-14T21:11:24Z',
                      'currentTotalPriceSet': {'shopMoney': {'amount': '4200.50', 'currencyCode': 'KES'}}},
    }

@override_settings(SHOPIFY_STORE='test-store.myshopify.com', SHOPIFY_ACCESS_TOKEN='fake-test-token')
class CustomerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('customer-reception')
        cls.staff = Staff.objects.create(name='Kelvin')
        cls.product = Product.objects.create(variant_id='gid://shopify/ProductVariant/1',title='Haircut',price='1500',kind='service')

    def setUp(self):
        self.client.force_login(self.user)
        self.bill = Bill.objects.create(created_by=self.user, first_name='Walk-in', notes='Keep this note')
        self.details = {'first_name':'Jane', 'last_name':'Doe', 'email':'jane@example.com', 'phone':'+254712345678'}

    def payment(self):
        Line.objects.create(bill=self.bill, product=self.product, title='Haircut', price='1500', staff=self.staff)
        return complete_payment(self.bill.pk, {'method':'cash','tendered':Decimal('1500'),'reference':''}, self.user)

    def create_response(self):
        node = shopify_customer()
        node.pop('lastOrder')
        return {'customerCreate': {'customer': node, 'userErrors': []}}

    @patch('pos.customers.graphql')
    def test_search_returns_and_caches_last_order_with_pagination(self, api):
        api.return_value = {'customers': {'nodes': [shopify_customer()], 'pageInfo': {'hasNextPage':True,'endCursor':'page2'}}}
        response = self.client.get(reverse('customer_search_api'), {'q':'Jane', 'after':'page1'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['customers'][0]['lastOrder']['id'], 'gid://shopify/Order/6513859199159')
        self.assertEqual(data['customers'][0]['lastOrder']['processedAt'], '2026-09-14T21:11:24+00:00')
        self.assertEqual(data['customers'][0]['lastOrder']['currentTotalPriceSet']['shopMoney']['amount'], '4200.50')
        self.assertEqual(api.call_args.args[1]['after'],'page1')
        self.assertEqual(data['pageInfo']['endCursor'], 'page2')
        self.assertIn('no-store',response['Cache-Control'])
        customer = Customer.objects.get()
        self.assertEqual(customer.last_order_amount, Decimal('4200.50'))
        detail = self.client.get(reverse('customer_detail_api', args=[customer.pk])).json()
        self.assertEqual(detail['lastOrder'], data['customers'][0]['lastOrder'])

    def test_missing_last_order_and_contacts_are_nullable(self):
        node=shopify_customer()
        node.update(lastOrder=None,defaultEmailAddress=None,defaultPhoneNumber=None)
        customer=cache_customer(node)
        self.assertIsNone(customer.as_api()['lastOrder'])
        self.assertEqual(customer.email,'')
        self.assertIsNone(customer.last_order_amount)

    @patch('pos.customers.graphql')
    def test_refresh_updates_existing_identity_and_clears_old_history(self,api):
        customer=cache_customer(shopify_customer())
        node=shopify_customer();node['lastOrder']=None
        api.return_value={'customer':node}
        updated=refresh_customer(customer)
        self.assertEqual(updated.pk,customer.pk)
        self.assertEqual(Customer.objects.count(),1)
        self.assertIsNone(updated.last_order_processed_at)
        self.assertIsNone(updated.last_order_amount)

    @patch('pos.customers.graphql',side_effect=TimeoutError)
    def test_search_failure_is_not_reported_as_empty_results(self,api):
        response=self.client.get(reverse('customer_search_api'),{'q':'Jane'})
        self.assertEqual(response.status_code,503)
        self.assertIn('error',response.json())
        self.assertNotIn('customers',response.json())

    @patch('pos.customers.graphql',side_effect=TimeoutError)
    def test_failed_refresh_preserves_previous_cache(self,api):
        customer=cache_customer(shopify_customer())
        response=self.client.post(reverse('customer_refresh_api',args=[customer.pk]))
        self.assertEqual(response.status_code,503)
        customer.refresh_from_db()
        self.assertEqual(customer.last_order_id,'gid://shopify/Order/6513859199159')

    @override_settings(SHOPIFY_ACCESS_TOKEN='')
    @patch('pos.customers.graphql')
    def test_unconfigured_search_and_create_never_call_shopify(self,api):
        self.assertEqual(self.client.get(reverse('customer_search_api'),{'q':'Jane'}).status_code,503)
        with self.assertRaises(ValueError):create_customer_for_bill(self.bill.pk,self.details,self.user)
        api.assert_not_called()
        self.assertFalse(CustomerCreation.objects.exists())

    @patch('pos.customers.graphql')
    def test_selection_refreshes_then_links_existing_customer(self,api):
        customer=cache_customer(shopify_customer())
        api.return_value={'customer':shopify_customer()}
        response=self.client.post(reverse('select_customer',args=[self.bill.pk]),{'customer_id':customer.pk})
        self.assertEqual(response.status_code,302)
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.shopify_customer_id,customer.pk)
        self.assertEqual(self.bill.first_name,'Jane')
        self.assertEqual(self.bill.notes,'Keep this note')
        self.assertContains(self.client.get(reverse('bill',args=[self.bill.pk])), '4200.50')
        order=self.payment().sync.payload['order']
        self.assertEqual(order['customer'], {'toAssociate':{'id':customer.shopify_id}})
        self.assertNotIn('toUpsert',order['customer'])

    def test_cached_customer_is_scoped_to_store(self):
        customer=cache_customer(shopify_customer())
        customer.store='other-store.myshopify.com';customer.save()
        self.assertEqual(self.client.get(reverse('customer_detail_api',args=[customer.pk])).status_code,404)
        self.assertEqual(self.client.post(reverse('select_customer',args=[self.bill.pk]),{'customer_id':customer.pk}).status_code,404)

    @patch('pos.customers.graphql')
    def test_create_success_and_repeat_submission_do_not_duplicate(self,api):
        api.return_value=self.create_response()
        customer=create_customer_for_bill(self.bill.pk,self.details,self.user)
        with self.assertRaises(ValueError):create_customer_for_bill(self.bill.pk,self.details,self.user)
        self.assertEqual(api.call_count,1)
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.shopify_customer_id,customer.pk)
        self.assertEqual(self.bill.customer_creation.state,'succeeded')
        self.assertIsNone(customer.as_api()['lastOrder'])
        self.assertEqual(api.call_args.args[1]['input']['firstName'],'Jane')

    @patch('pos.customers.graphql')
    def test_duplicate_email_errors_are_visible_and_do_not_link(self,api):
        api.return_value={'customerCreate':{'customer':None,'userErrors':[{'field':['input','email'],'message':'Email has already been taken'}]}}
        data={'action':'create_shopify_customer', **{'new-'+k:v for k,v in self.details.items()}}
        response=self.client.post(reverse('bill',args=[self.bill.pk]),data)
        self.assertContains(response,'Email has already been taken')
        self.bill.refresh_from_db()
        self.assertIsNone(self.bill.shopify_customer_id)
        self.assertEqual(self.bill.customer_creation.state,'rejected')

    @patch('pos.customers.graphql',side_effect=TimeoutError)
    def test_uncertain_creation_is_not_retried_or_paid(self,api):
        with self.assertRaises(ValueError):create_customer_for_bill(self.bill.pk,self.details,self.user)
        with self.assertRaises(ValueError):create_customer_for_bill(self.bill.pk,self.details,self.user)
        self.assertEqual(api.call_count,1)
        self.assertEqual(self.bill.customer_creation.state,'review')
        with self.assertRaises(ValueError):self.payment()
        self.bill.refresh_from_db();self.assertEqual(self.bill.status,'open')
        customer=cache_customer(shopify_customer())
        attach_customer(self.bill.pk,customer,self.user)
        self.assertEqual(CustomerCreation.objects.get(bill=self.bill).state,'resolved')

    def test_stale_sending_can_be_resolved_by_customer_selection(self):
        customer=cache_customer(shopify_customer())
        attempt=CustomerCreation.objects.create(bill=self.bill,state='sending')
        with self.assertRaises(ValueError):attach_customer(self.bill.pk,customer,self.user)
        CustomerCreation.objects.filter(pk=attempt.pk).update(updated_at=timezone.now()-timedelta(minutes=3))
        attach_customer(self.bill.pk,customer,self.user)
        attempt.refresh_from_db();self.assertEqual(attempt.state,'resolved')

    @patch('pos.customers.graphql')
    def test_create_form_validation_does_not_make_api_request(self,api):
        response=self.client.post(reverse('bill',args=[self.bill.pk]),{'action':'create_shopify_customer','new-first_name':'Jane','new-phone':'0712'})
        self.assertContains(response,'international phone number')
        api.assert_not_called()

    @patch('pos.customers.graphql')
    def test_create_from_sale_form_selects_customer(self,api):
        api.return_value=self.create_response()
        data={'action':'create_shopify_customer', **{'new-'+k:v for k,v in self.details.items()}}
        response=self.client.post(reverse('bill',args=[self.bill.pk]),data)
        self.assertEqual(response.status_code,302)
        self.bill.refresh_from_db();self.assertIsNotNone(self.bill.shopify_customer_id)

    def test_linked_contact_fields_cannot_be_overwritten_locally(self):
        attach_customer(self.bill.pk,cache_customer(shopify_customer()),self.user)
        self.client.post(reverse('bill',args=[self.bill.pk]),{'first_name':'Wrong','email':'wrong@example.com','notes':'Updated'})
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.first_name,'Jane')
        self.assertEqual(self.bill.notes,'Updated')

    def test_completed_bills_cannot_change_customer(self):
        customer=cache_customer(shopify_customer())
        self.payment()
        response=self.client.post(reverse('select_customer',args=[self.bill.pk]),{'customer_id':customer.pk})
        self.assertEqual(response.status_code,302)
        with self.assertRaises(ValueError):attach_customer(self.bill.pk,customer,self.user)
        with self.assertRaises(ValueError):create_customer_for_bill(self.bill.pk,self.details,self.user)

    def test_customer_routes_require_authentication_and_csrf(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('customer_search_api'),{'q':'Jane'}).status_code,302)
        protected=Client(enforce_csrf_checks=True)
        protected.force_login(self.user)
        self.assertEqual(protected.post(reverse('select_customer',args=[self.bill.pk]),{'customer_id':1}).status_code,403)

    def test_blank_short_and_long_search_terms_rejected(self):
        for term in ['', 'J', 'x'*121]:
            self.assertEqual(self.client.get(reverse('customer_search_api'),{'q':term}).status_code,400)

    def test_search_supports_full_name_email_phone_and_escapes_operators(self):
        self.assertEqual(search_expression('Jane Doe'), '(first_name:"Jane" OR last_name:"Jane") AND (first_name:"Doe" OR last_name:"Doe")')
        self.assertEqual(search_expression('jane@example.com'), 'email:"jane@example.com"')
        self.assertEqual(search_expression('+254 712-345-678'), 'phone:"+254712345678"')
        self.assertIn(r'\"', search_expression('Jane"'))
        self.assertIn(r'\:', search_expression('tag:vip'))

    @override_settings(SHOPIFY_LIVE_SYNC=True)
    @patch('pos.customers.refresh_customer',side_effect=TimeoutError)
    @patch('pos.shopify.graphql')
    def test_history_refresh_failure_does_not_resend_successful_order(self,api,refresh):
        from .shopify import send_job
        attach_customer(self.bill.pk,cache_customer(shopify_customer()),self.user)
        job=self.payment().sync
        api.return_value={'orderCreate':{'order':{'id':'gid://shopify/Order/88'},'userErrors':[]}}
        self.assertTrue(send_job(job.pk))
        self.assertFalse(send_job(job.pk))
        self.assertEqual(SyncJob.objects.get(pk=job.pk).state,'synced')
        self.assertEqual(api.call_count,1)
