# Karim POS — Django API + SvelteKit

A local salon and spa reception app. SvelteKit provides the reception interface; Django owns authentication, validation, payments, local records, and the Shopify integration.

## Open the app

The development preview uses **http://127.0.0.1:8765/app/**. Choose **Explore the local demo** on the login page. Demo access is restricted to loopback connections and is disabled when `DEMO_MODE=0`.

Demo data contains eight products/services and six staff members. Sample prices are illustrative KES values, not the client's confirmed price list. The browser acceptance test leaves one explicitly named Demo Customer sale in the local database.

## Install and run

Tested with Python 3.14 and Django 6.1.1 already available on this computer. Use a virtual environment for further development. From this directory:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
Set-Location frontend
corepack pnpm install
corepack pnpm build
Set-Location ..
python manage.py migrate
python manage.py seed_demo
python manage.py runserver 127.0.0.1:8765
```

The compiled Svelte bundle is served by Django, so production needs one application server. Rebuild from `frontend/` after changing Svelte source. On subsequent runs, `start.ps1` applies migrations and starts the local preview. It does not install dependencies, rebuild the frontend, or seed data.

For individual logins and administration:

```powershell
python manage.py createsuperuser
```

Use `/admin/` to create users and maintain staff profiles. Sale and payment records are read-only there so they cannot bypass the checkout workflow.

## What works

- Reception dashboard with actual local totals, open bills, and queue counts.
- Service and retail catalog search and category filtering.
- Mixed bills, customer details, quantities, price overrides, per-item discounts.
- Independent staff selection for every line, including retail. Repeated products remain separate so different staff can be assigned.
- Open bills can be resumed or voided; paid bills are immutable through the app.
- Cash, M-Pesa, and card payment recording with explicit manual confirmation.
- Cash change calculation; exact amounts required for card and M-Pesa.
- One payment and one queued payload per completed bill, saved in one database transaction.
- Printable receipt, daily reports, staff sales attribution, CSV export, and an audit trail.
- Paginated Shopify catalog importer and GraphQL order sender, with local queue visibility.
- Authentication, CSRF protection, server-side amount checks, and secrets outside source control.

Save customer details and each changed bill line before moving on. Staff assignment is integrated into the bill screen, rather than a separate page. The workflow is New sale → Open bill → Checkout → Receipt → Orders & sync.

## Connect Shopify later

### Customer search and creation

The sale page now supports live Shopify customer search by name, email, or phone, plus customer creation and automatic selection. A shared customer cache stores the latest accessible order ID, processed date, current order total/currency, and refresh timestamp. Selected customers are linked to completed Shopify orders by ID using `customer.toAssociate.id`; the older upsert mapping below applies only to unlinked local customers.

Run `python manage.py migrate` and restart the app after updating an existing installation. Customer search needs `read_customers`, creation needs `write_customers`, and latest-order lookup needs order-read access plus applicable protected-customer-data permissions. Customer creation writes to Shopify immediately, independently of `SHOPIFY_LIVE_SYNC`. Demo mode does not simulate this customer API when real credentials are configured.

See [CUSTOMERS.md](CUSTOMERS.md) for endpoints, response fields, freshness, failure handling, and test results.

Put credentials in the local `.env` file. Do not commit it or paste tokens into the UI:

```dotenv
SHOPIFY_STORE=your-store.myshopify.com
SHOPIFY_ACCESS_TOKEN=your-admin-api-access-token
SHOPIFY_LIVE_SYNC=0
POS_CURRENCY=KES
```

Restart the app after changing configuration. The token must be an Admin API access token; an app client ID/secret alone is not that token. OAuth installation/token refresh is not implemented yet. Use a development store first.

Catalog queries need product/inventory read access. Order creation needs order write access and appropriate customer-data access for the installed app. Confirm actual scopes and protected customer data permissions during installation; do not grant unrelated scopes simply because they appear as alternatives in schema tooling.

Pull products with **Services & catalog → Pull Shopify catalog**, or:

```powershell
python manage.py pull_catalog
```

The importer reads all product-variant pages, checks shop currency, updates the local cache atomically, and deactivates missing/archived variants. An interrupted import preserves the previous catalog. A successful import hides demo products. Services are identified by a case-insensitive `service` product tag or the product type `Service`; confirm this convention against the actual store. Catalog creation/editing remains in Shopify.

After validating the development-store mapping and resolving the live-use items below, set `SHOPIFY_LIVE_SYNC=1`. Send individual orders from the queue or run:

```powershell
python manage.py sync_shopify
```

This command makes one pass over pending jobs. It is not an automatically installed background worker. A scheduled task/service can invoke it later; failed jobs require manual retry. Demo-product orders are always blocked from Shopify submission.

## Shopify field mapping

The integration uses Admin GraphQL `2026-07`.

| Requested field | Implementation |
|---|---|
| Name | `order.name`, e.g. `#KP-1001` |
| Inventory Behaviour | `options.inventoryBehaviour`, default `DECREMENT_OBEYING_POLICY` |
| Created At | Original POS timestamp in order custom attributes; completion timestamp in `processedAt`. Shopify generates its own `createdAt`. |
| Additional Details | `customAttributes` with POS UUID, original creation time, payment reference and payment method; notes in `note` |
| Customer: First Name | `customer.toUpsert.firstName`; optional last name, email, phone |
| Line: Quantity | `lineItems.quantity` |
| Line: Price | Original price retained in properties; net unit price sent in `priceSet` |
| Line: Discount per Item | `properties`, named `Discount per item` |
| Line: Total | `properties`, named `Line total`; Shopify derives totals from net prices and quantities |
| Line: Properties | Staff name, stable local staff ID, original price, discount, total |
| Transaction: Amount | `transactions.amountSet.shopMoney`; a successful manually collected sale |

**Discount limitation:** `orderCreate` does not offer arbitrary native line discount inputs. This version sends net prices and keeps original prices/discounts as properties. Shopify native discount reports will therefore not reflect these reductions as discounts. Select a native discount/draft-order strategy before live use if those reports are required.

The persisted payload snapshots line prices and staff names at checkout, so renaming staff or refreshing the catalog will not alter an already queued payload. Customer upsert behavior must be tested against existing customers, especially phone-only or name-only sales.

## Sync failure handling

- A conditional database update claims each queued order before sending, preventing two local workers from submitting it concurrently.
- A successful order ID is persisted and the job cannot be resent through the app.
- Explicit Shopify validation errors set the job to Failed, visible in the queue.
- A timeout or uncertain response sets Needs review. It is **never automatically retried**, because Shopify may already have created the order.
- A process crash during transmission can leave a Sending job. Treat it as uncertain as well.
- Match the `Karim POS ID` order custom attribute or `sourceIdentifier` in Shopify before recovery. The static `karim-pos` tag identifies the source without embedding the UUID. This version intentionally has no UI reset button for uncertain jobs.

This prevents blind duplicates; it is not a claim of end-to-end exactly-once delivery. Do not manually reset uncertain jobs without establishing whether Shopify accepted them.

## Still needed before live salon deployment

1. Confirm currency, tax rates, inclusive/exclusive pricing, receipt requirements, and discount reporting. **No tax is calculated in this version.** Do not enable live checkout under taxable business rules until implemented.
2. Confirm the salon's Shopify inventory location, service inventory policy, fulfillment behavior, stock validation, reservations, and stock-conflict handling. Current cached stock is an aggregate across locations; local checkout does not decrement it or prevent overselling. The sender requests Shopify inventory handling only when an order is submitted.
3. Integrate the chosen payment provider and authenticated payment-status callbacks. M-Pesa/card entries currently record receptionist confirmation; they do not charge or independently verify funds.
4. Add refunds, partial/split payments if required, and manager approval/role rules for discounts, voids, staff maintenance, and sync actions. Currently any authenticated reception user shares the operational workspace.
5. Add catalog webhooks or scheduled refresh, sync scheduling, monitoring, and uncertain-job reconciliation. No scheduler is installed.
6. Plan deployment: production application server, HTTPS, `DJANGO_DEBUG=0`, `DEMO_MODE=0`, a strong secret, explicit hosts, individual logins, database backups and restore testing. SQLite is the local development database; use PostgreSQL for multiple active reception terminals and load-test concurrency.
7. Confirm appointments, revenue splits, commissions, consumable-product usage, and hardware integration separately. They are not implemented based solely on screenshot labels.

## Validation

```powershell
python manage.py test
python manage.py check
```

The test suite covers payment totals, cash change, discounts, missing staff, duplicate checkout, immutable completed bills, payment references, authenticated routes, demo restrictions, catalog pagination, failed imports, CSV formula escaping, and sync uncertainty. Tests use mocked Shopify responses and do not contact a store.

Browser acceptance: create a mixed service/retail bill, save customer and staff selections, apply a line discount, complete a demo cash payment, and inspect receipt/queue. Desktop and narrow layouts are checked locally.

## Source layout

- `pos/models.py`: local records and database constraints.
- `pos/services.py`: transactional checkout.
- `pos/shopify.py`: catalog query, order payload, queue sender.
- `pos/api.py`, `pos/api_urls.py`: authenticated JSON API used by SvelteKit.
- `frontend/src/`: SvelteKit reception interface and responsive styles.
- `pos/static/frontend/`: compiled SvelteKit bundle served by Django.
- `pos/views.py`, `pos/templates/pos/`: retained legacy Django screens during migration.
- `pos/management/commands/`: demo data, catalog import, queue processing.
- `pos/tests.py`: automated regression tests.

## Shopify references

- [orderCreate](https://shopify.dev/docs/api/admin-graphql/latest/mutations/orderCreate)
- [OrderCreateOrderInput](https://shopify.dev/docs/api/admin-graphql/latest/input-objects/OrderCreateOrderInput)
- [OrderCreateLineItemInput](https://shopify.dev/docs/api/admin-graphql/latest/input-objects/OrderCreateLineItemInput)
- [OrderCreateOrderTransactionInput](https://shopify.dev/docs/api/admin-graphql/latest/input-objects/OrderCreateOrderTransactionInput)
- [productVariants](https://shopify.dev/docs/api/admin-graphql/latest/queries/productVariants)
