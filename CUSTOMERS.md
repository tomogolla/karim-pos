# Shopify customers and latest order data

## Reception workflow

1. Open **Create a sale** and search Shopify by name, email, or international phone number. Results are paginated 25 at a time.
2. Select the correct profile. Selection re-fetches it from Shopify, saves its ID on the bill, and copies contact details into the local sale snapshot.
3. The selected card shows the latest accessible order date and current order total. **Refresh latest order** updates the history without discarding line-item edits.
4. If the customer is not found, expand **Customer not found? Add a new customer**. Enter first name and at least an email or international phone number. The app creates the customer in Shopify and selects the new profile. No marketing subscription is added.

Selected Shopify contact details cannot be overwritten through the local walk-in form. Sale notes remain editable. Removing a selection does not delete the customer from Shopify.

**Customer creation writes immediately**, independently of the completed-order sync switch. With real credentials, `DEMO_MODE` does not simulate customer writes. Browser acceptance tests used a separate database and simulated Shopify service.

## Shared authenticated API

All endpoints require a Django login session. Responses use `Cache-Control: no-store`. POST requests require Django's CSRF token.

| Method | Path | Behavior |
|---|---|---|
| GET | `/api/customers/?q=Jane&after=<optional cursor>` | Live search, cache returned profiles/history, return `customers` and `pageInfo`. |
| GET | `/api/customers/<localId>/` | Read cached customer and last-order fields for another app screen. |
| POST | `/api/customers/<localId>/refresh/` | Fetch current customer/history, update cache, return refreshed profile. |
| POST | `/bills/<billId>/customer/select/` | Form field `customer_id=<localId>` selects the customer on an open bill. |
| POST | `/bills/<billId>/customer/clear/` | Remove the selection from an open bill. |
| POST | `/bills/<billId>/` | `action=create_shopify_customer` with `new-first_name`, `new-last_name`, `new-email`, `new-phone` creates and selects a profile. Returns form errors or redirects on success. |

Illustrative customer response:

```json
{
  "localId": 1,
  "id": "gid://shopify/Customer/123",
  "displayName": "Jane Sample",
  "firstName": "Jane",
  "lastName": "Sample",
  "email": "jane@example.com",
  "phone": "+254712345678",
  "lastOrder": {
    "id": "gid://shopify/Order/6513859199159",
    "processedAt": "2026-09-14T21:11:24+00:00",
    "currentTotalPriceSet": {
      "shopMoney": {"amount": "4200.50", "currencyCode": "KES"}
    }
  },
  "fetchedAt": "2026-09-16T07:00:00+00:00"
}
```

`processedAt` is a timezone-aware ISO timestamp; `+00:00` and `Z` both represent UTC. Amounts are decimal strings. The UI displays dates in the configured/browser local timezone.

`lastOrder` is null when Shopify provides no accessible last order. Do not interpret null as zero spend or proof that the customer has never ordered: permissions and accessible order history affect the result.

The amount is the **current order total**, not a verified payment amount or lifetime spend. Other screens can use `id` and `processedAt` independently and check `fetchedAt` before assuming the cache is fresh.

## Storage and identity

`Customer` is keyed by Shopify store domain and customer ID. It stores contact data plus `last_order_id`, `last_order_processed_at`, `last_order_amount`, `last_order_currency`, and `fetched_at`. `Bill.shopify_customer` links to this shared record; the bill keeps its own contact snapshot.

Completed orders use `customer.toAssociate.id` for selected customers, preventing the order sender from creating another profile or changing the selected Shopify customer's name. Unlinked walk-ins retain the existing upsert behavior. Already queued payloads are not altered by subsequent cache refreshes.

Search, selection, and explicit refresh update the cache. After successful order sync, the sender also attempts a history refresh. If that read fails, the order stays Synced, old cached history remains, and the audit trail records the deferred refresh. A failed history read never triggers an order resend.

## Errors and duplicate protection

- Search/refresh failures return HTTP 503 with an `error` string; they are not displayed as empty results. Invalid search length returns 400. Session expiry is detected by the UI.
- Store-scoped lookups prevent selecting a cached customer from another configured store.
- Shopify validation errors, including duplicate email/phone, appear on the creation form.
- A durable attempt per bill prevents concurrent/repeated creation submissions.
- Timeouts and uncertain results block another create attempt and checkout. Search Shopify by email/phone and select the matching profile to resolve the attempt.
- A crashed Sending attempt can be resolved by a fresh verified selection after two minutes. No create request is automatically resent.
- After one successful creation on a bill, search/select rather than creating another from that same bill.
- This protects against double submission and blind retry. It is not a global exactly-once guarantee across different bills. Search before creating a profile.

## Setup and validation

Use the existing `SHOPIFY_STORE` and `SHOPIFY_ACCESS_TOKEN`. The app needs customer read/write, order read, and applicable protected-customer-data permissions. Credentials remain server-side. Apply migration `0002` with `python manage.py migrate`, then restart the application.

Validation completed: 38 automated tests passed, including 20 customer tests; Shopify schema validation passed for search, detail, and creation operations; isolated browser checks passed for search, selection, latest-order display/refresh, no results, and customer creation. Actual store permissions and responses still need live integration verification. No real customer was created during testing.

Implementation: `pos/customers.py`, `pos/models.py`, `pos/views.py`, `pos/templates/pos/customer_picker.html`, `pos/static/pos/app.js`, and `pos/test_customers.py`.

References: [Customer](https://shopify.dev/docs/api/admin-graphql/latest/objects/Customer), [customers query](https://shopify.dev/docs/api/admin-graphql/latest/queries/customers), [customerCreate](https://shopify.dev/docs/api/admin-graphql/latest/mutations/customerCreate).
