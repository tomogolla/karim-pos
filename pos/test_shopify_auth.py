import io
import json
from urllib.error import HTTPError
from urllib.parse import parse_qs
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from . import shopify


def response(payload):
    return io.BytesIO(json.dumps(payload).encode())


@override_settings(
    SHOPIFY_STORE='test-store.myshopify.com',
    SHOPIFY_CLIENT_ID='client-id',
    SHOPIFY_CLIENT_SECRET='client-secret',
    SHOPIFY_ACCESS_TOKEN='',
    SHOPIFY_API_VERSION='2026-07',
)
class ShopifyTokenTests(SimpleTestCase):
    def setUp(self):
        shopify._reset_token_cache()

    def tearDown(self):
        shopify._reset_token_cache()

    @patch('pos.shopify.urlopen')
    def test_client_credentials_are_exchanged_and_cached(self, open_url):
        open_url.return_value = response({
            'access_token': 'short-lived-token', 'scope': 'read_products', 'expires_in': 86399,
        })

        self.assertEqual(shopify.access_token(), 'short-lived-token')
        self.assertEqual(shopify.access_token(), 'short-lived-token')
        self.assertEqual(open_url.call_count, 1)
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, 'https://test-store.myshopify.com/admin/oauth/access_token')
        self.assertEqual(parse_qs(request.data.decode()), {
            'grant_type': ['client_credentials'],
            'client_id': ['client-id'],
            'client_secret': ['client-secret'],
        })

    @patch('pos.shopify.time.monotonic', side_effect=[100, 100, 141, 141])
    @patch('pos.shopify.urlopen')
    def test_token_refreshes_before_expiry(self, open_url, monotonic):
        open_url.side_effect = [
            response({'access_token': 'first-token', 'expires_in': 100}),
            response({'access_token': 'second-token', 'expires_in': 100}),
        ]

        self.assertEqual(shopify.access_token(), 'first-token')
        self.assertEqual(shopify.access_token(), 'second-token')
        self.assertEqual(open_url.call_count, 2)

    @patch('pos.shopify.urlopen')
    def test_graphql_refreshes_and_retries_once_after_401(self, open_url):
        unauthorized = HTTPError('https://test-store.myshopify.com', 401, 'Unauthorized', {}, io.BytesIO())
        open_url.side_effect = [
            response({'access_token': 'expired-token', 'expires_in': 86399}),
            unauthorized,
            response({'access_token': 'replacement-token', 'expires_in': 86399}),
            response({'data': {'shop': {'name': 'Test Store'}}}),
        ]

        result = shopify.graphql('query { shop { name } }', {})

        self.assertEqual(result, {'shop': {'name': 'Test Store'}})
        self.assertEqual(open_url.call_count, 4)
        retry_request = open_url.call_args_list[-1].args[0]
        self.assertEqual(retry_request.get_header('X-shopify-access-token'), 'replacement-token')

    @override_settings(SHOPIFY_CLIENT_ID='', SHOPIFY_CLIENT_SECRET='', SHOPIFY_ACCESS_TOKEN='legacy-token')
    @patch('pos.shopify.urlopen')
    def test_legacy_access_token_remains_supported(self, open_url):
        open_url.return_value = response({'data': {'shop': {'name': 'Legacy Store'}}})

        result = shopify.graphql('query { shop { name } }', {})

        self.assertEqual(result, {'shop': {'name': 'Legacy Store'}})
        request = open_url.call_args.args[0]
        self.assertEqual(request.get_header('X-shopify-access-token'), 'legacy-token')

    @patch('pos.shopify.urlopen')
    def test_invalid_token_response_is_rejected(self, open_url):
        open_url.return_value = response({'scope': 'read_products', 'expires_in': 86399})
        with self.assertRaisesMessage(ValueError, 'invalid access token response'):
            shopify.access_token()

