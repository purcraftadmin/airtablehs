# WooCommerce Shared Inventory Sync

A FastAPI service that acts as the **Single Source of Truth (SSOT)** for stock across 4–6 WooCommerce sites. When any site receives an order, stock decrements once (idempotent), and the new quantity is pushed to **every** other site automatically.

---

## Architecture

```
WC Site 1 ──webhook──┐
WC Site 2 ──webhook──┤                        ┌── WC REST API ──► Site 1
WC Site 3 ──webhook──┼──► FastAPI Service ────┤   (propagation)  Site 2
WC Site 4 ──webhook──┘         │              └── WC REST API ──► Site 3..N
                                ▼
                          PostgreSQL SSOT
                          (stock / events)
                                │
                    (optional)  ▼
                          Airtable (reports)
```

### Key design decisions

| Concern | Solution |
|---|---|
| Idempotency | `UNIQUE(site_id, order_id, line_item_id, event_type)` – duplicate webhooks are no-ops |
| Concurrency | `SELECT … FOR UPDATE` row-lock before every stock mutation |
| Negative stock | Clamped to 0 unless `backorders=TRUE` in `products` table |
| Propagation | asyncio queue → background worker with exponential-backoff retries |
| Dead letters | Failed propagations logged to `propagation_failures` table |
| Auth | HMAC-SHA256 (WooCommerce native) **or** Bearer token |
| Admin UI | Session-based auth (bcrypt passwords, HTTP-only cookie) |
| Secrets at rest | Fernet AES-128-CBC – credentials never stored in plaintext |
| Airtable | Optional, best-effort, non-blocking – never SSOT |

---

## Project layout

```
.
├── app/
│   ├── main.py                       # FastAPI app + lifespan + bootstrap
│   ├── config.py                     # Settings from .env
│   ├── database.py                   # Async SQLAlchemy engine
│   ├── models.py                     # ORM models (inventory + admin)
│   ├── schemas.py                    # Pydantic schemas
│   ├── deps.py                       # Webhook signature verification
│   ├── routers/
│   │   ├── webhooks.py               # POST /webhooks/woocommerce/*
│   │   └── admin.py                  # GET /admin/health, /stock (JSON API)
│   ├── admin/
│   │   ├── auth.py                   # bcrypt, session helpers, flash
│   │   ├── crypto.py                 # Fernet encrypt/decrypt
│   │   ├── deps.py                   # require_admin dependency
│   │   ├── templates_cfg.py          # Shared Jinja2Templates instance
│   │   └── routers/
│   │       ├── auth_routes.py        # GET/POST /admin/login, /admin/logout
│   │       ├── dashboard.py          # GET /admin (dashboard)
│   │       ├── sites.py              # CRUD /admin/sites/*
│   │       ├── settings_routes.py    # GET/POST /admin/settings
│   │       └── audit.py              # GET /admin/audit
│   ├── services/
│   │   ├── inventory.py              # Stock mutation (transactional)
│   │   ├── propagation.py            # Background queue + WC push (reads DB sites)
│   │   ├── wc_client.py              # WooCommerce REST API client
│   │   ├── mapping.py                # SKU ↔ product_id mapping
│   │   └── airtable.py               # Optional Airtable writer
│   ├── templates/                    # Jinja2 HTML templates
│   │   ├── base.html
│   │   ├── login.html
│   │   ├── dashboard.html
│   │   ├── settings.html
│   │   ├── audit.html
│   │   └── sites/
│   │       ├── list.html
│   │       └── form.html
│   └── static/
│       └── admin.css                 # Black & white admin theme
├── cli/
│   └── refresh_mappings.py           # CLI tool
├── migrations/
│   ├── init.sql                      # Core inventory schema
│   └── 002_admin.sql                 # admin_users, app_settings, sites tables
├── mu-plugins/
│   └── wc-inventory-webhook-transform.php
├── tests/
│   ├── conftest.py
│   ├── test_stock.py
│   ├── test_idempotency.py
│   ├── test_webhooks.py
│   ├── test_crypto.py                # Encryption roundtrip tests
│   ├── test_admin_auth.py            # Login/logout/access tests
│   └── test_admin_sites.py           # Site CRUD tests
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── requirements-dev.txt
└── .env.example
```

---

## Quick-start (Docker)

### 1. Clone and configure

```bash
git clone <this-repo> wc-inventory-sync
cd wc-inventory-sync
cp .env.example .env
```

Edit `.env` – **required fields:**

```bash
# 1. Generate session signing secret
SESSION_SECRET_KEY=$(openssl rand -hex 32)

# 2. Generate Fernet key for credential encryption
CONFIG_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

# 3. Set the bootstrap admin credentials (used on first startup only)
BOOTSTRAP_ADMIN_USER=admin
BOOTSTRAP_ADMIN_PASSWORD=yourStrongPasswordHere

# 4. Generate webhook secret
WEBHOOK_SHARED_SECRET=$(openssl rand -hex 32)

# 5. Optional: pre-seed sites from env (migrated to DB on first startup)
# SITES='[{"site_id":"shop1","base_url":"https://shop1.example.com","wc_key":"ck_...","wc_secret":"cs_..."}]'
```

### 2. Start services

```bash
docker compose up -d
```

Both SQL migrations (`init.sql` and `002_admin.sql`) run automatically on first Postgres start.

The API is available at `http://localhost:8000`.
Health check: `curl http://localhost:8000/admin/health`

---

## Admin UI – step-by-step setup

### Step 1 – Log in

Open `http://localhost:8000/admin` in your browser.
You will be redirected to the login page. Sign in with `BOOTSTRAP_ADMIN_USER` / `BOOTSTRAP_ADMIN_PASSWORD`.

### Step 2 – Add your first WooCommerce site

1. Click **Sites** in the sidebar → **+ Add Site**
2. Fill in:
   - **Display Name**: human-readable label
   - **Site ID**: short unique slug (e.g. `shop1`) – must match what your WC webhook sends as `site_id`
   - **Base URL**: `https://shop1.example.com`
   - **Consumer Key / Secret**: from WooCommerce → Settings → Advanced → REST API
3. Click **Add Site**
4. Repeat for each site (up to 30)

### Step 3 – Configure webhooks on each WooCommerce site

For each site, go to **WooCommerce → Settings → Advanced → Webhooks → Add Webhook** and create two:

| Field | Webhook 1 | Webhook 2 |
|---|---|---|
| Name | Inventory Sync – Order | Inventory Sync – Refund |
| Status | Active | Active |
| Topic | Order updated | Order updated |
| Delivery URL | `https://your-service/webhooks/woocommerce/order_paid` | `https://your-service/webhooks/woocommerce/refund_or_cancel` |
| Secret | `WEBHOOK_SHARED_SECRET` value | same |

Also install the `mu-plugins/wc-inventory-webhook-transform.php` file on each WC site (see Connecting WooCommerce Sites below) and add `define('WC_INV_SITE_ID', 'shop1');` to `wp-config.php`.

### Step 4 – Refresh SKU mappings

After adding a site, click **Sync SKUs** on the Sites page, or run:

```bash
# All sites
docker compose exec api python -m cli.refresh_mappings

# Single site
docker compose exec api python -m cli.refresh_mappings --site shop1
```

Or via the API:
```bash
curl -X POST http://localhost:8000/admin/refresh-mappings/shop1
```

### Step 5 – Configure settings (optional)

Click **Settings** in the sidebar to adjust:
- **Decrement on Status** (default: `processing`)
- **Backorders default**
- **Webhook Auth Mode** (HMAC or Bearer)
- **Airtable integration**

Changes take effect immediately without restart.

---

## Admin Routes reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/admin/login` | Login page |
| `POST` | `/admin/login` | Submit credentials |
| `POST` | `/admin/logout` | End session |
| `GET` | `/admin` | Dashboard (stats + recent activity) |
| `GET` | `/admin/sites` | Sites list with search |
| `GET` | `/admin/sites/new` | New site form |
| `POST` | `/admin/sites` | Create site |
| `GET` | `/admin/sites/{id}/edit` | Edit site form |
| `POST` | `/admin/sites/{id}` | Update site |
| `POST` | `/admin/sites/{id}/deactivate` | Toggle active state |
| `POST` | `/admin/sites/{id}/refresh-mapping` | Sync SKUs for site |
| `GET` | `/admin/settings` | Settings form |
| `POST` | `/admin/settings` | Save settings |
| `GET` | `/admin/audit` | Inventory events + propagation failures |

---

## Build SKU mappings (required before webhooks work)

```bash
# All sites
docker compose exec api python -m cli.refresh_mappings

# Single site
docker compose exec api python -m cli.refresh_mappings --site shop1

# List current mappings
docker compose exec api python -m cli.refresh_mappings --list

# Show live stock levels
docker compose exec api python -m cli.refresh_mappings --stock
```

Or via the API:

```bash
# All sites
curl -X POST http://localhost:8000/admin/refresh-mappings

# Single site
curl -X POST http://localhost:8000/admin/refresh-mappings/shop1
```

---

## Connecting WooCommerce Sites

For **each** WooCommerce site, create two webhooks pointing to your service.

### Step-by-step (WP Admin UI)

1. Log in → **WooCommerce → Settings → Advanced → Webhooks → Add webhook**

2. **Webhook 1 – Order Paid**
   | Field | Value |
   |---|---|
   | Name | Inventory Sync – Order Paid |
   | Status | Active |
   | Topic | Order updated *(or "Order created" if you want it earlier)* |
   | Delivery URL | `https://your-service.example.com/webhooks/woocommerce/order_paid` |
   | Secret | `<value of WEBHOOK_SHARED_SECRET>` |
   | API Version | WP REST API Integration v3 |

3. **Webhook 2 – Refund / Cancel**
   | Field | Value |
   |---|---|
   | Name | Inventory Sync – Refund/Cancel |
   | Status | Active |
   | Topic | Order updated |
   | Delivery URL | `https://your-service.example.com/webhooks/woocommerce/refund_or_cancel` |
   | Secret | `<value of WEBHOOK_SHARED_SECRET>` |

> **Note:** WooCommerce sends the webhook with a `X-WC-Webhook-Signature` header (HMAC-SHA256, base64-encoded). The service verifies this automatically.

### WP-CLI alternative

```bash
# On each WC site server:
wp wc webhook create \
  --user=1 \
  --name="Inventory Sync Order Paid" \
  --topic="order.updated" \
  --delivery_url="https://your-service.example.com/webhooks/woocommerce/order_paid" \
  --secret="<WEBHOOK_SHARED_SECRET>" \
  --status=active

wp wc webhook create \
  --user=1 \
  --name="Inventory Sync Refund Cancel" \
  --topic="order.updated" \
  --delivery_url="https://your-service.example.com/webhooks/woocommerce/refund_or_cancel" \
  --secret="<WEBHOOK_SHARED_SECRET>" \
  --status=active
```

### Webhook payload format

The service expects the payload from your WooCommerce webhook to be **transformed** before delivery. You have two options:

**Option A (recommended):** Use a lightweight WP `mu-plugin` to transform the native WC webhook payload:

```php
<?php
// mu-plugins/wc-inventory-webhook-transform.php
add_filter('woocommerce_webhook_payload', function($payload, $resource, $resource_id, $webhook_id) {
    if ($resource !== 'order') return $payload;

    $order = wc_get_order($resource_id);
    if (!$order) return $payload;

    $site_id = defined('WC_INV_SITE_ID') ? WC_INV_SITE_ID : get_option('blogname');
    $line_items = [];

    foreach ($order->get_items() as $item_id => $item) {
        $product = $item->get_product();
        $sku = $product ? $product->get_sku() : '';
        if (!$sku) continue;
        $line_items[] = [
            'line_item_id' => (string)$item_id,
            'sku'          => $sku,
            'qty'          => (int)$item->get_quantity(),
        ];
    }

    return [
        'site_id'    => $site_id,
        'order_id'   => (string)$resource_id,
        'status'     => $order->get_status(),
        'line_items' => $line_items,
    ];
}, 10, 4);
```

Add `define('WC_INV_SITE_ID', 'shop1');` to `wp-config.php` for each site.

**Option B:** Use a WooCommerce webhook with a custom `delivery_url` that rewrites through a small proxy. (More complex; Option A preferred.)

---

## API Reference

### Webhooks

| Method | Path | Description |
|---|---|---|
| `POST` | `/webhooks/woocommerce/order_paid` | Receive order event → decrement stock → propagate |
| `POST` | `/webhooks/woocommerce/refund_or_cancel` | Receive refund/cancel → increment stock → propagate |

### Admin

| Method | Path | Description |
|---|---|---|
| `GET` | `/admin/health` | Health check (DB ping) |
| `GET` | `/admin/stock` | List all SKU stock levels |
| `GET` | `/admin/stock/{sku}` | Stock for a single SKU |
| `POST` | `/admin/refresh-mappings` | Refresh mappings for all sites |
| `POST` | `/admin/refresh-mappings/{site_id}` | Refresh mappings for one site |

### Interactive docs

```
http://localhost:8000/docs
```

---

## Database Schema

```sql
products(sku PK, name, lead_time_days, reorder_point, backorders)
stock(sku PK → products, on_hand, reserved, updated_at)
site_sku_map(site_id, sku → products, product_id, variation_id)
inventory_events(id, site_id, order_id, line_item_id, sku, delta, event_type, created_at)
  UNIQUE(site_id, order_id, line_item_id, event_type)   ← idempotency key
propagation_failures(id, site_id, sku, payload, error, attempts, created_at)
```

---

## Airtable (optional)

Set these in `.env`:

```
AIRTABLE_API_KEY=patXXXX
AIRTABLE_BASE_ID=appXXXX
AIRTABLE_TABLES_JSON='{"stock":"tblSTOCK_TABLE_ID","events":"tblEVENTS_TABLE_ID"}'
```

Expected Airtable table schemas:

**Stock table** (upserted on every change):
| Field | Type |
|---|---|
| SKU | Single line text (primary) |
| On Hand | Number |
| 7d Avg Daily Sales | Number |
| 30d Avg Daily Sales | Number |
| Last 50 Txn Summary | Long text |
| Updated At | Single line text |

**Events table** (append-only):
| Field | Type |
|---|---|
| Site | Single line text |
| Order ID | Single line text |
| SKU | Single line text |
| Delta | Number |
| Event Type | Single line text |
| On Hand After | Number |
| Timestamp | Single line text |

---

## Database Schema

```sql
-- Core inventory (init.sql)
products(sku PK, name, lead_time_days, reorder_point, backorders)
stock(sku PK → products, on_hand, reserved, updated_at)
site_sku_map(site_id, sku → products, product_id, variation_id)
inventory_events(id, site_id, order_id, line_item_id, sku, delta, event_type, created_at)
  UNIQUE(site_id, order_id, line_item_id, event_type)   ← idempotency key
propagation_failures(id, site_id, sku, payload, error, attempts, created_at)

-- Admin UI (002_admin.sql)
admin_users(id UUID, username UNIQUE, password_hash, is_active, created_at)
app_settings(id=1, decrement_status, backorders_default, webhook_auth_mode,
             airtable_enabled, airtable_base_id, airtable_table_names,
             airtable_api_key_encrypted, updated_at)
sites(id UUID, site_id UNIQUE, name, base_url,
      wc_key_encrypted, wc_secret_encrypted, is_active,
      created_at, updated_at, last_sync_at)
```

## Running Tests

```bash
# Install test + dev dependencies
pip install -r requirements-dev.txt

# Run all tests
pytest -v

# With coverage
pytest --cov=app --cov-report=term-missing
```

---

## Production Checklist

- [ ] Use a reverse proxy (nginx/Caddy) with TLS; set `https_only=True` in `SessionMiddleware`
- [ ] Set `SESSION_SECRET_KEY` to a 64-char random hex string
- [ ] Set `CONFIG_ENCRYPTION_KEY` and **back it up securely** – losing it means losing stored credentials
- [ ] Set `WEBHOOK_SHARED_SECRET` to a 64-char random hex string
- [ ] Set `BOOTSTRAP_ADMIN_USER` + `BOOTSTRAP_ADMIN_PASSWORD` then unset after first login
- [ ] Restrict `/admin/*` UI to your IP range at the nginx/Caddy level
- [ ] Set Postgres connection pooling appropriately for your load
- [ ] Monitor `propagation_failures` table for stuck jobs (visible in Admin → Audit)
- [ ] Run `refresh-mappings` after any product/SKU changes on any site
- [ ] Set up a cron job to periodically re-sync stock (full reconciliation):
  ```bash
  # Example: reconcile every hour
  0 * * * * docker compose exec -T api python -m cli.refresh_mappings
  ```
- [ ] Enable `backorders=TRUE` in the `products` table for any SKU that allows overselling

---

## Scaling notes

- The propagation worker runs inside the same process. For high volume, move it to a dedicated worker container using the same queue mechanism (or upgrade to Redis + Celery/arq).
- Multiple API replicas are safe: stock mutations use row-level locking (`SELECT … FOR UPDATE`), and idempotency is enforced at the DB layer.
