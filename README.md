# Karim POS — Django API + SvelteKit

A salon and spa reception app. SvelteKit provides the reception interface; Django owns authentication, validation, payments, local records, and the Shopify integration. In production it runs behind nginx on its own HTTPS subdomain, with gunicorn as the application server and PostgreSQL as the database.

## Local development

Requires Python 3.12 or newer (Django 6.1) and, for frontend changes, Node.js with pnpm. From this directory:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.development.example .env
Set-Location frontend
pnpm install
pnpm build
Set-Location ..
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_demo
python manage.py runserver 127.0.0.1:8765
```

Open **http://127.0.0.1:8765/app/** and sign in with the account you created. On later runs, `start.ps1` applies migrations and starts the local server (it sets `DJANGO_DEBUG=1`; it does not install dependencies, rebuild the frontend, or seed data).

- **Database:** with `DJANGO_DEBUG=1` and no `DJANGO_DB_NAME`, the app uses SQLite (`db.sqlite3`). Fill in the `DJANGO_DB_*` values in `.env` to use a local PostgreSQL instead, which is closer to production.
- **Sample data:** `seed_demo` adds eight sample products/services and six staff members. Sample prices are illustrative KES values, not the client's confirmed price list. The command only runs when `DJANGO_DEBUG=1`. Sample products are flagged as demo items and are always blocked from being sent to Shopify; a real catalog import deactivates them.
- **Logins:** there is no demo login. Every user signs in with their own account. Use the Django admin (default `/admin/`, configurable with `DJANGO_ADMIN_URL`) to create users and maintain staff profiles. Sale and payment records are read-only there so they cannot bypass the checkout workflow. For now every signed-in user can do everything; roles are not implemented yet.
- **Frontend:** the compiled SvelteKit bundle is committed under `pos/static/frontend/` and served by Django. After **any** change in `frontend/src/`, run `pnpm build` in `frontend/` and commit the regenerated files in `pos/static/frontend/` together with the source change. Otherwise the server keeps serving the old interface.

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

Run `python manage.py migrate` and restart the app after updating an existing installation. Customer search needs `read_customers`, creation needs `write_customers`, and latest-order lookup needs order-read access plus applicable protected-customer-data permissions. Customer creation writes to Shopify immediately, independently of `SHOPIFY_LIVE_SYNC`. There is no simulated mode: with real credentials, customer creation always reaches Shopify.

See [CUSTOMERS.md](CUSTOMERS.md) for endpoints, response fields, freshness, failure handling, and test results.

Put credentials in the `.env` file (on the server: the production `.env`). Do not commit it or paste tokens into the UI:

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

The importer reads all product-variant pages, checks shop currency, updates the local cache atomically, and deactivates missing/archived variants. An interrupted import preserves the previous catalog. A successful import hides sample (`seed_demo`) products. Services are identified by a case-insensitive `service` product tag or the product type `Service`; confirm this convention against the actual store. Catalog creation/editing remains in Shopify.

After validating the development-store mapping and resolving the live-use items below, set `SHOPIFY_LIVE_SYNC=1`. Send individual orders from the queue or run:

```powershell
python manage.py sync_shopify
```

This command makes one pass over pending jobs. It is not an automatically installed background worker. A scheduled task/service can invoke it later; failed jobs require manual retry. Orders containing sample (`seed_demo`) products are always blocked from Shopify submission.

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
6. Finish deployment (see **Deployment notes** below): nginx and systemd configuration, database backups and restore testing, enabling HSTS once HTTPS is confirmed, and a concurrency check with several active reception terminals.
7. Confirm appointments, revenue splits, commissions, consumable-product usage, and hardware integration separately. They are not implemented based solely on screenshot labels.

## Production configuration

All configuration comes from environment variables, read from the real environment first and then from a `.env` file in the project directory. Start from [.env.example](.env.example), which is the production template. The app **refuses to start** with a clear `ImproperlyConfigured` error when debug mode is off and a required value is missing; it never falls back to debug mode, open hosts, SQLite, or demo access.

| Variable | Required | Meaning |
|---|---|---|
| `DJANGO_DEBUG` | – | Must be `0` (or unset) in production. Only the exact value `1` enables debug mode. |
| `DJANGO_SECRET_KEY` | yes | Long random secret used to sign sessions and tokens. |
| `DJANGO_ALLOWED_HOSTS` | yes | Hostname(s) the app answers to, comma-separated, e.g. `pos.example.com`. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | yes | Same hostname(s) with `https://`, e.g. `https://pos.example.com`. Entries without `https://` are rejected. |
| `DJANGO_BEHIND_PROXY` | set to `1` | Trust `X-Forwarded-Proto` and `X-Forwarded-For` from nginx. Only set it when nginx overwrites those headers (see below). |
| `DJANGO_DB_NAME`, `DJANGO_DB_USER`, `DJANGO_DB_PASSWORD`, `DJANGO_DB_HOST`, `DJANGO_DB_PORT` | yes | PostgreSQL connection. Host defaults to `localhost`, port to `5432`. |
| `DJANGO_ADMIN_URL` | recommended | Path of the Django admin, default `admin/`. Move it to a hard-to-guess path in production. |
| `DJANGO_SECURE_HSTS_SECONDS` | – | HSTS duration, default `0`. Raise it only after HTTPS is confirmed working end to end; browsers remember it and will refuse plain HTTP for that long. |
| `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` | – | `1` adds `includeSubDomains` to HSTS. Default off. |
| `DJANGO_SECURE_SSL_REDIRECT` | – | Default `0`, because nginx redirects HTTP to HTTPS. Turning it on without the forwarded-proto header causes redirect loops. |
| `AXES_FAILURE_LIMIT`, `AXES_COOLOFF_MINUTES` | – | Login lockout: failures allowed per username + IP (default 5) and lockout length (default 30 minutes). |
| `DJANGO_LOG_LEVEL` | – | Console log level, default `INFO`. |
| `GUNICORN_PORT` | – | Local port gunicorn listens on, default `8765`. |
| `POS_CURRENCY`, `POS_TIME_ZONE` | – | Default `KES` and `Africa/Nairobi`. |
| `SHOPIFY_STORE`, `SHOPIFY_ACCESS_TOKEN`, `SHOPIFY_LIVE_SYNC`, `SHOPIFY_INVENTORY_BEHAVIOUR` | – | Shopify connection (see above). `SHOPIFY_STORE`, when set, must look like `your-store.myshopify.com`. Keep `SHOPIFY_LIVE_SYNC=0` until validation is complete. |

Security behaviour with debug off: session and CSRF cookies are `Secure`; the session cookie is `HttpOnly` and `SameSite=Lax`; the CSRF cookie is deliberately readable by JavaScript because the SvelteKit client sends it back in the `X-CSRFToken` header. Sessions last 12 hours. Passwords need at least 12 characters and cannot be numeric-only, common, or similar to the username.

**Login lockout:** after 5 failed sign-ins for the same username from the same IP address, that combination is locked for 30 minutes. This covers the reception login, the legacy login page and the Django admin login. The reception screen shows "Too many failed sign-in attempts…". To unlock someone early: `python manage.py axes_reset_username <username>` (or `axes_reset` to clear all lockouts).

**Logging:** everything is written to the console (stdout/stderr), so gunicorn and systemd capture it. A logging filter removes Shopify tokens, the secret key, the database password, password values, email addresses and phone numbers from log lines and tracebacks. The gunicorn access log records request paths without query strings, because customer searches put names, phone numbers and emails in `?q=`.

## Deployment notes

Server setup, nginx and systemd configuration are a separate step. This is what that step must provide.

**Application**

1. Python 3.12+, a virtualenv, and `pip install -r requirements.txt`.
2. A PostgreSQL database and user, and a production `.env` (from `.env.example`) owned by the app user with `chmod 600`.
3. `python manage.py migrate` (this also creates the login-lockout tables), `python manage.py collectstatic --noinput`, and `python manage.py createsuperuser`.
4. Run `gunicorn config.wsgi:application -c gunicorn.conf.py` from the project directory, under systemd. [gunicorn.conf.py](gunicorn.conf.py) binds to `127.0.0.1:$GUNICORN_PORT` only (default 8765) with 3 workers and a 60-second timeout. It is never exposed publicly.
5. After a deploy, check `https://pos.example.com/healthz`. It returns `{"status": "ok"}` when the app and database respond, and `503 {"status": "unavailable"}` otherwise. From the server itself, send the right Host header: `curl -H "Host: pos.example.com" http://127.0.0.1:8765/healthz`.

**What nginx must provide** (on the `pos.example.com` server block)

- **HTTPS** with a valid certificate, and a redirect from HTTP to HTTPS.
- **Proxy to gunicorn:** `proxy_pass http://127.0.0.1:8765;`
- **Host header:** `proxy_set_header Host $host;`. Django rejects any host not in `DJANGO_ALLOWED_HOSTS`. Do not make this the `default_server`, so requests for other or unknown hostnames never reach the app.
- **X-Forwarded-For:** `proxy_set_header X-Forwarded-For $remote_addr;`. This must **overwrite** whatever the client sent. The login lockout counts failures per IP address taken from this header. If nginx passed a client-supplied value through, an attacker could fake a new IP address on every attempt and never be locked out.
- **X-Forwarded-Proto:** `proxy_set_header X-Forwarded-Proto $scheme;`. This must also be set by nginx and overwrite any client value. Django uses it to know the request arrived over HTTPS (secure cookies, CSRF checks). If it is missing, logins fail CSRF checks; if clients could set it, they could make plain HTTP look secure.
- **Static files:** serve `/static/` directly from the `staticfiles/` directory that `collectstatic` fills (for example `location /static/ { alias /path/to/karim-pos/staticfiles/; }`). The compiled interface under `/app/` (including `/app/_app/` assets) is served by Django; nginx just proxies it.
- **Privacy in nginx logs:** nginx's default access log includes query strings, which can contain customer search terms. Use a log format with `$uri` instead of `$request` / `$request_uri` for this site.

`DJANGO_BEHIND_PROXY=1` is only safe once nginx overwrites both forwarded headers as described.

**Frontend changes:** the compiled bundle in `pos/static/frontend/` is part of the repository. Rebuild it with `pnpm build` in `frontend/` and commit it with every frontend change, then run `collectstatic` again on the server.

## Pesapal readiness (not implemented)

No payment provider is integrated yet. When Pesapal (or another provider) is added, its payment notification ("IPN" or callback) endpoint will be different from every other endpoint in this app:

- It must be a **public, unauthenticated URL**, because the provider's servers call it, not a signed-in receptionist.
- It must be **exempt from CSRF protection**, because the provider cannot send the app's CSRF token.
- It must **never trust the callback payload**. Anyone can send a request to a public URL. The callback should only be treated as a hint: the app must look up the payment status by querying the provider's API with its own credentials, and only then mark a sale as paid.

## Validation

```powershell
python manage.py test
python manage.py check
```

The test suite covers payment totals, cash change, discounts, missing staff, duplicate checkout, immutable completed bills, payment references, authenticated routes, catalog pagination, failed imports, CSV formula escaping, sync uncertainty, production settings that must fail closed, log redaction, login lockout, CSRF handling and the health check. Tests use mocked Shopify responses and do not contact a store. Run them against SQLite (default with `DJANGO_DEBUG=1`) and against PostgreSQL by setting the `DJANGO_DB_*` variables.

Before deploying, run `python manage.py check --deploy` with the production environment. Two warnings are expected until the owner confirms HTTPS works: `security.W004` (HSTS not set) and `security.W008` (SSL redirect off, because nginx redirects). After `DJANGO_SECURE_HSTS_SECONDS` is raised, `security.W005` (subdomains) and `security.W021` (preload) may appear; both are optional decisions for the domain owner.

Browser acceptance: create a mixed service/retail bill, save customer and staff selections, apply a line discount, complete a cash payment, and inspect receipt/queue. Desktop and narrow layouts are checked locally.

## Source layout

- `pos/models.py`: local records and database constraints.
- `pos/services.py`: transactional checkout.
- `pos/shopify.py`: catalog query, order payload, queue sender.
- `pos/api.py`, `pos/api_urls.py`: authenticated JSON API used by SvelteKit.
- `frontend/src/`: SvelteKit reception interface and responsive styles.
- `pos/static/frontend/`: compiled SvelteKit bundle served by Django.
- `pos/views.py`, `pos/templates/pos/`: retained legacy Django screens during migration.
- `pos/management/commands/`: local sample data, catalog import, queue processing.
- `config/settings.py`: all configuration, read from the environment.
- `config/security.py`: login-lockout and CSRF-failure hooks; `config/log_redaction.py`: log filter.
- `gunicorn.conf.py`: production application server settings.
- `pos/tests.py`, `pos/test_*.py`: automated regression tests.

## Shopify references

- [orderCreate](https://shopify.dev/docs/api/admin-graphql/latest/mutations/orderCreate)
- [OrderCreateOrderInput](https://shopify.dev/docs/api/admin-graphql/latest/input-objects/OrderCreateOrderInput)
- [OrderCreateLineItemInput](https://shopify.dev/docs/api/admin-graphql/latest/input-objects/OrderCreateLineItemInput)
- [OrderCreateOrderTransactionInput](https://shopify.dev/docs/api/admin-graphql/latest/input-objects/OrderCreateOrderTransactionInput)
- [productVariants](https://shopify.dev/docs/api/admin-graphql/latest/queries/productVariants)
