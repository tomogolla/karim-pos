import json

from django.conf import settings
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings

from config.security import client_ip


@override_settings(AXES_FAILURE_LIMIT=5)
class LoginLockoutTests(TestCase):
    def setUp(self):
        User.objects.create_user('reception', password='correct-horse-battery')
        User.objects.create_user('manager', password='another-long-password')

    def login(self, username, password, **extra):
        return self.client.post('/api/session/login/', data=json.dumps({'username': username, 'password': password}),
                                content_type='application/json', **extra)

    def test_api_login_locks_after_repeated_failures(self):
        for _ in range(4):
            self.assertEqual(self.login('reception', 'wrong').status_code, 400)
        locked = self.login('reception', 'wrong')
        self.assertEqual(locked.status_code, 429)
        self.assertTrue(locked.json()['locked'])
        self.assertIn('Too many failed sign-in attempts', locked.json()['error'])
        self.assertEqual(locked['Retry-After'], str(30 * 60))
        # The correct password is refused while the lockout lasts.
        self.assertEqual(self.login('reception', 'correct-horse-battery').status_code, 429)
        self.assertFalse(self.client.get('/api/session/').json()['authenticated'])
        # Lockout is per username + IP: another user from the same address still signs in.
        self.assertEqual(self.login('manager', 'another-long-password').status_code, 200)
        self.assertTrue(self.client.get('/api/session/').json()['authenticated'])

    def test_same_username_from_another_address_is_not_locked(self):
        for _ in range(5):
            self.login('reception', 'wrong')
        self.assertEqual(self.login('reception', 'correct-horse-battery', REMOTE_ADDR='10.0.0.9').status_code, 200)

    def test_success_resets_failure_count(self):
        for _ in range(4):
            self.login('reception', 'wrong')
        self.assertEqual(self.login('reception', 'correct-horse-battery').status_code, 200)
        self.client.logout()
        self.assertEqual(self.login('reception', 'wrong').status_code, 400)

    def test_admin_login_is_protected(self):
        url = f'/{settings.ADMIN_URL}login/'
        User.objects.create_superuser('owner', password='owner-long-password')
        for _ in range(5):
            response = self.client.post(url, {'username': 'owner', 'password': 'wrong'})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(self.client.post(url, {'username': 'owner', 'password': 'owner-long-password'}).status_code, 429)


class ClientIpTests(TestCase):
    def request(self, forwarded=None):
        extra = {'REMOTE_ADDR': '127.0.0.1'}
        if forwarded is not None:
            extra['HTTP_X_FORWARDED_FOR'] = forwarded
        return RequestFactory().post('/api/session/login/', **extra)

    @override_settings(BEHIND_PROXY=False)
    def test_forwarded_header_ignored_without_proxy_flag(self):
        self.assertEqual(client_ip(self.request('203.0.113.7')), '127.0.0.1')

    @override_settings(BEHIND_PROXY=True)
    def test_behind_proxy_uses_address_added_by_nginx(self):
        self.assertEqual(client_ip(self.request('203.0.113.7')), '203.0.113.7')
        # A client-supplied value is on the left; nginx appends the real address on the right.
        self.assertEqual(client_ip(self.request('1.2.3.4, 203.0.113.7')), '203.0.113.7')
        self.assertEqual(client_ip(self.request('not-an-ip')), '127.0.0.1')
        self.assertEqual(client_ip(self.request()), '127.0.0.1')
