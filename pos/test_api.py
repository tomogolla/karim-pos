import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase

from .models import Bill, Line, Product, Staff, SyncJob


class SvelteApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('svelte-reception', password='secure-password')
        self.staff = Staff.objects.create(name='Kelvin', role='Stylist')
        self.product = Product.objects.create(
            variant_id='gid://shopify/ProductVariant/900', title='Classic haircut',
            sku='CUT-1', category='Hair', kind='service', price=Decimal('1500.00'))

    def json_request(self, method, path, payload=None):
        return getattr(self.client, method)(path, data=json.dumps(payload or {}), content_type='application/json')

    def test_svelte_shell_and_assets_are_served(self):
        response = self.client.get('/app/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-cache, no-store, must-revalidate')
        html = b''.join(response.streaming_content).decode()
        self.assertIn('/app/_app/', html)
        nested = self.client.get('/app/sales/123')
        self.assertEqual(nested.status_code, 200)
        self.assertIn('/app/_app/', b''.join(nested.streaming_content).decode())
        self.assertEqual(self.client.get('/app/_app/version.json').status_code, 200)

    def test_session_login_and_protected_api(self):
        self.assertFalse(self.client.get('/api/session/').json()['authenticated'])
        self.assertEqual(self.client.get('/api/dashboard/').status_code, 401)
        response = self.json_request('post', '/api/session/login/', {
            'username': 'svelte-reception', 'password': 'secure-password'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.client.get('/api/session/').json()['authenticated'])
        self.assertEqual(self.client.get('/api/dashboard/').status_code, 200)

    def test_sale_api_preserves_checkout_rules(self):
        self.client.force_login(self.user)
        response = self.json_request('post', '/api/bills/')
        self.assertEqual(response.status_code, 201)
        bill = response.json()
        self.assertEqual(bill['lines'], [])

        response = self.json_request('post', f"/api/bills/{bill['id']}/lines/", {'productId': self.product.pk})
        self.assertEqual(response.status_code, 201)
        line = response.json()
        response = self.json_request('patch', f"/api/lines/{line['id']}/", {
            'quantity': 1, 'price': '1500.00', 'discount': '100.00', 'staffId': self.staff.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['total'], '1400.00')

        response = self.json_request('post', f"/api/bills/{bill['id']}/checkout/", {
            'method': 'cash', 'tendered': '1500.00', 'reference': '', 'confirmed': True})
        self.assertEqual(response.status_code, 200)
        completed = response.json()
        self.assertEqual(completed['status'], 'paid')
        self.assertEqual(completed['payment']['change'], '100.00')
        self.assertEqual(completed['sync']['state'], 'pending')
        self.assertEqual(SyncJob.objects.get(bill_id=bill['id']).payload['order']['tags'], ['karim-pos'])

    def test_bill_update_and_void_are_limited_to_open_bills(self):
        self.client.force_login(self.user)
        bill = Bill.objects.create(created_by=self.user)
        response = self.json_request('patch', f'/api/bills/{bill.pk}/', {
            'first_name': 'Amina', 'last_name': 'Noor', 'phone': '+254700000000',
            'email': 'amina@example.com', 'notes': 'First visit'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['customer'], 'Amina Noor')
        self.assertEqual(self.client.delete(f'/api/bills/{bill.pk}/').status_code, 200)
        bill.refresh_from_db()
        self.assertEqual(bill.status, 'void')
        self.assertEqual(self.json_request('patch', f'/api/bills/{bill.pk}/', {}).status_code, 409)

    def test_csrf_is_required_for_mutating_api_calls(self):
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.user)
        protected.get('/api/session/')
        self.assertEqual(protected.post('/api/bills/', data='{}', content_type='application/json').status_code, 403)
