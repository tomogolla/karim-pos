# DigitalOcean and GitHub Actions deployment

This guide is written for the existing Ubuntu 24.04 droplet. It preserves the
existing `vendorops` nginx site and PostgreSQL installation. Karim POS listens
only on `127.0.0.1:8765` and gets a separate nginx server block selected by its
domain name.

## What the pasted commands already completed

- PostgreSQL 16 and nginx are running.
- The `karimpos` system user and `/srv/karim-pos` exist.
- The `karim_pos` database and database role exist.
- `/srv/karim-pos/app` is a working Git clone using a read-only GitHub deploy
  key stored on the droplet.

The virtualenv command in the transcript contained the command twice on one
line. That is why `/srv/karim-pos/venv/bin/pip` did not exist. Do not create
that old shared venv now: `deploy/deploy.sh` creates one isolated venv per
release.

## 1. Rotate the exposed database credential

The database password appeared in the pasted terminal transcript. Treat it as
exposed and replace it before starting the application:

```bash
sudo -u postgres psql
\password karim_pos
\q
```

Use the new value only in `/srv/karim-pos/shared/.env`. Do not put it in GitHub
or the repository.

## 2. Point a domain at the droplet

Create an `A` record such as `pos.example.com` pointing to the droplet's public
IPv4 address. Wait until this returns that address:

```bash
getent ahostsv4 pos.example.com
```

The domain must differ from the domain used by the existing nginx application.

## 3. Add swap on this 1 GB droplet

The transcript shows no swap and very little available memory. Add a 1 GB swap
file before the first dependency installation:

```bash
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
sudo swapon --show
```

If `/swapfile` already exists, inspect it with `sudo swapon --show` instead of
running these creation commands again.

## 4. Install the checked-in server configuration

After this deployment code is pushed to `prod-hardening`, update the existing
clone and run the installer with the real POS domain:

```bash
sudo -u karimpos git -C /srv/karim-pos/app fetch origin prod-hardening
sudo -u karimpos git -C /srv/karim-pos/app checkout prod-hardening
sudo -u karimpos git -C /srv/karim-pos/app pull --ff-only origin prod-hardening
sudo bash /srv/karim-pos/app/deploy/install-server.sh pos.example.com
```

The installer adds a new nginx site; it does not replace or disable any
existing site. It also changes the system user's shell to `/bin/bash` so SSH
deployments can run and gives that user permission to restart only
`karim-pos.service` without a password.

Fill every empty required value in the shared environment file:

```bash
sudo nano /srv/karim-pos/shared/.env
sudo chown karimpos:karimpos /srv/karim-pos/shared/.env
sudo chmod 600 /srv/karim-pos/shared/.env
```

Required values include `DJANGO_SECRET_KEY`, the new
`DJANGO_DB_PASSWORD`, `DJANGO_ALLOWED_HOSTS` and
`DJANGO_CSRF_TRUSTED_ORIGINS`. The installer fills both domain settings. Keep
`SHOPIFY_LIVE_SYNC=0` until the store validation is complete. Generate a Django
secret with `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`.

`GUNICORN_WORKERS=1` is intentional on this shared 1 GB server.

### Configure Shopify automatic token refresh

New Shopify Dev Dashboard apps use a Client ID and Client Secret instead of a
token copied from the admin. Karim POS exchanges those credentials for a
24-hour Admin API token, caches it in each running process, and requests a new
token 60 seconds before expiry. If Shopify rejects a cached token with HTTP
401, the app obtains a replacement and safely retries that rejected request
once. No daily cron job, token file, or manual token update is required.

The client-credentials flow only works when the app and store are in the same
Shopify organization:

1. Open `https://dev.shopify.com/`, select the organization that owns the
   store, and create an app with **Start from Dev Dashboard**.
2. Create an app version. For a non-embedded app, the default app URL
   `https://shopify.dev/apps/default-app-home` is sufficient.
3. Request only the required Admin API scopes. This integration currently
   needs product and inventory reads, customer reads and writes, order reads,
   and order writes: `read_products`, `read_inventory`, `read_customers`,
   `write_customers`, `read_orders`, and `write_orders`. Shopify customer and
   order data can also require protected-customer-data approval for the fields
   used by this app.
4. Release the version, install the app on the target store, and approve the
   requested access.
5. On the app's **Settings** page, copy the Client ID and Client Secret. Do not
   paste either value into GitHub, application logs, or this repository.
6. Edit the existing shared environment file on the droplet:

```bash
sudo nano /srv/karim-pos/shared/.env
sudo chown karimpos:karimpos /srv/karim-pos/shared/.env
sudo chmod 600 /srv/karim-pos/shared/.env
```

Set the Shopify section as follows, using the permanent `*.myshopify.com`
domain rather than a custom storefront domain:

```dotenv
SHOPIFY_STORE=your-store.myshopify.com
SHOPIFY_CLIENT_ID=your-client-id
SHOPIFY_CLIENT_SECRET=your-client-secret
SHOPIFY_ACCESS_TOKEN=
SHOPIFY_LIVE_SYNC=0
SHOPIFY_INVENTORY_BEHAVIOUR=DECREMENT_OBEYING_POLICY
```

Both client credential values must be present. `SHOPIFY_ACCESS_TOKEN` is only
for an existing legacy admin-created custom app and should remain blank for a
new Dev Dashboard app. Restart both processes after editing the environment:

```bash
sudo systemctl restart karim-pos.service
sudo systemctl restart karim-pos-sync.service
sudo journalctl -u karim-pos.service -n 50 --no-pager
```

The oneshot sync service might be inactive after a successful run; that is
normal. Sign in to Karim POS and pull the Shopify catalog while
`SHOPIFY_LIVE_SYNC=0`. A successful import verifies the store domain,
credential exchange, granted product scopes, and API connectivity without
sending an order. Then test customer search and creation on a development
store. Only enable live order sync after all required permissions and field
mappings have been validated.

If Shopify returns `shop_not_permitted`, confirm that both the target store and
app appear under the same organization in the Dev Dashboard. A client store in
another organization requires an OAuth installation flow instead of this
client-credentials setup. See Shopify's official
[Dev Dashboard app guide](https://shopify.dev/docs/apps/build/dev-dashboard/create-apps-using-dev-dashboard)
and [client-credentials guide](https://shopify.dev/docs/apps/build/authentication-authorization/client-credentials-grant).

## 5. Create a separate GitHub Actions SSH key

The existing key on the droplet is for **droplet to GitHub** access. CI needs a
different key for **GitHub to droplet** access. Create it on the Windows
workstation in PowerShell:

```powershell
ssh-keygen -t ed25519 -f "$HOME\.ssh\karim_pos_actions" -C "github-actions-karim-pos"
Get-Content "$HOME\.ssh\karim_pos_actions.pub" | ssh root@DROPLET_IP "install -d -o karimpos -g karimpos -m 700 /srv/karim-pos/.ssh; touch /srv/karim-pos/.ssh/authorized_keys; cat >> /srv/karim-pos/.ssh/authorized_keys; chown karimpos:karimpos /srv/karim-pos/.ssh/authorized_keys; chmod 600 /srv/karim-pos/.ssh/authorized_keys"
ssh -i "$HOME\.ssh\karim_pos_actions" karimpos@DROPLET_IP whoami
```

The final command must print `karimpos`.

Verify the server's SSH host key out of band before adding it to GitHub. On the
droplet, display the authoritative fingerprint:

```bash
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

On the workstation, compare it with:

```powershell
ssh-keyscan -t ed25519 DROPLET_IP | ssh-keygen -lf -
```

After the fingerprints match, capture the complete known-hosts line:

```powershell
ssh-keyscan -H -t ed25519 DROPLET_IP
```

## 6. Configure GitHub

In the repository, open **Settings > Environments** and create `production`.
Add these environment secrets:

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | Droplet public IP address or SSH hostname |
| `DEPLOY_SSH_PRIVATE_KEY` | Complete contents of `~/.ssh/karim_pos_actions` (the private key) |
| `DEPLOY_KNOWN_HOSTS` | Complete verified hashed line from `ssh-keyscan -H` |

Add a repository Actions variable named `DEPLOY_ENABLED` with value `true` only
after the server setup above is finished. Until then, pushes run CI and skip the
deployment job. Restrict the `production` environment to the
`prod-hardening` branch and add a required reviewer if the repository plan
supports that rule.

The workflow runs the Django tests, checks for missing migrations, validates
the Svelte frontend, rebuilds it, and verifies that the generated bundle was
committed. It then deploys the exact 40-character commit SHA that passed CI.

Run the first deployment from **Actions > Test and deploy > Run workflow**.
The deployment creates `/srv/karim-pos/releases/<commit>`, updates the
`/srv/karim-pos/current` symlink, restarts Gunicorn, checks `/healthz`, and
keeps the five newest releases. A failed application health check restores the
previous application symlink. Database migrations are not automatically
reversed, so migrations must remain backward compatible with the preceding
release.

## 7. Create the first application administrator

After the first successful workflow run:

```bash
sudo -u karimpos /srv/karim-pos/current/venv/bin/python /srv/karim-pos/current/manage.py createsuperuser
sudo systemctl status karim-pos --no-pager
curl -H 'Host: pos.example.com' http://127.0.0.1:8765/healthz
```

The curl response must be `{"status": "ok"}`.

## 8. Enable HTTPS

Once DNS resolves and the HTTP health check works:

```bash
sudo certbot --nginx -d pos.example.com
sudo nginx -t
sudo systemctl reload nginx
curl --fail https://pos.example.com/healthz
```

Choose Certbot's HTTP-to-HTTPS redirect. Confirm login, logout, the Svelte
application and static assets over HTTPS. Then raise
`DJANGO_SECURE_HSTS_SECONDS` gradually and restart `karim-pos.service`.

## 9. Enable scheduled Shopify delivery later

Keep the timer disabled during store validation. After the Shopify credentials,
permissions, inventory location and a test order are verified, set
`SHOPIFY_LIVE_SYNC=1`, then run:

```bash
sudo systemctl restart karim-pos.service
sudo systemctl enable --now karim-pos-sync.timer
systemctl list-timers karim-pos-sync.timer
```

The command sends only jobs in the `pending` state. Outcomes marked for review
are not automatically retried.

## Operations

```bash
# Application logs
sudo journalctl -u karim-pos.service -n 100 --no-pager

# Scheduled Shopify sync logs
sudo journalctl -u karim-pos-sync.service -n 100 --no-pager

# nginx validation and logs
sudo nginx -t
sudo tail -n 100 /var/log/nginx/karim-pos.error.log

# Database backup
sudo install -d -o postgres -g postgres -m 0700 /var/backups/karim-pos
sudo -u postgres pg_dump --format=custom --file=/var/backups/karim-pos/karim-pos-$(date +%F).dump karim_pos
```

The server reports that a restart is required. Schedule `sudo reboot` after
checking the existing application and Karim POS are both configured to start at
boot. Verify both sites after the reboot.
