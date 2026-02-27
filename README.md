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
| Airtable | Optional, best-effort, non-blocking – never SSOT |

---

## Project layout

```
.
├── app/
│   ├── main.py                  # FastAPI app + lifespan
│   ├── config.py                # Settings from .env
│   ├── database.py              # Async SQLAlchemy engine
│   ├── models.py                # ORM models
│   ├── schemas.py               # Pydantic schemas
│   ├── deps.py                  # Webhook signature verification
│   ├── routers/
│   │   ├── webhooks.py          # POST /webhooks/woocommerce/*
│   │   └── admin.py             # GET/POST /admin/*
│   └── services/
│       ├── inventory.py         # Stock mutation (transactional)
│       ├── propagation.py       # Background queue + WC push
│       ├── wc_client.py         # WooCommerce REST API client
│       ├── mapping.py           # SKU ↔ product_id mapping
│       └── airtable.py          # Optional Airtable writer
├── cli/
│   └── refresh_mappings.py      # CLI tool
├── migrations/
│   └── init.sql                 # Postgres schema
├── tests/
│   ├── conftest.py
│   ├── test_stock.py
│   ├── test_idempotency.py
│   └── test_webhooks.py
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
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

Edit `.env`:

```bash
# Fill in your sites
SITES='[
  {"site_id":"shop1","base_url":"https://shop1.example.com","wc_key":"ck_...","wc_secret":"cs_..."},
  {"site_id":"shop2","base_url":"https://shop2.example.com","wc_key":"ck_...","wc_secret":"cs_..."}
]'

# Generate a strong secret
WEBHOOK_SHARED_SECRET=$(openssl rand -hex 32)
```

### 2. Start services

```bash
docker compose up -d
```

The API is available at `http://localhost:8000`.
Health check: `curl http://localhost:8000/admin/health`

### 3. Build SKU mappings (required before webhooks work)

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

## Running Tests

```bash
# Install test deps
pip install -r requirements.txt
pip install aiosqlite

# Run all tests
pytest -v

# With coverage
pip install pytest-cov
pytest --cov=app --cov-report=term-missing
```

---

## Production Checklist

- [ ] Use a reverse proxy (nginx/Caddy) with TLS in front of the service
- [ ] Set `WEBHOOK_SHARED_SECRET` to a 64-char random hex string
- [ ] Restrict `/admin/*` endpoints with IP allowlisting or an auth middleware
- [ ] Set Postgres connection pooling appropriately for your load
- [ ] Monitor `propagation_failures` table for stuck jobs
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
