-- WooCommerce Shared Inventory SSOT Schema
-- Run once on fresh Postgres instance

CREATE TABLE IF NOT EXISTS products (
    sku             TEXT PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT '',
    lead_time_days  INT  NOT NULL DEFAULT 0,
    reorder_point   INT  NOT NULL DEFAULT 0,
    backorders      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS stock (
    sku         TEXT PRIMARY KEY REFERENCES products(sku) ON DELETE CASCADE,
    on_hand     INT NOT NULL DEFAULT 0,
    reserved    INT NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Per-site mapping: sku -> WooCommerce product_id (and optional variation_id)
CREATE TABLE IF NOT EXISTS site_sku_map (
    site_id      TEXT NOT NULL,
    sku          TEXT NOT NULL REFERENCES products(sku) ON DELETE CASCADE,
    product_id   BIGINT NOT NULL,
    variation_id BIGINT,             -- NULL if simple product
    refreshed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (site_id, sku)
);

CREATE TABLE IF NOT EXISTS inventory_events (
    id           BIGSERIAL PRIMARY KEY,
    site_id      TEXT        NOT NULL,
    order_id     TEXT        NOT NULL,
    line_item_id TEXT        NOT NULL,
    sku          TEXT        NOT NULL,
    delta        INT         NOT NULL,   -- negative = decrement
    event_type   TEXT        NOT NULL,   -- 'order_paid' | 'refund' | 'cancel'
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (site_id, order_id, line_item_id, event_type)
);

-- Dead-letter table for failed propagation jobs
CREATE TABLE IF NOT EXISTS propagation_failures (
    id          BIGSERIAL PRIMARY KEY,
    site_id     TEXT        NOT NULL,
    sku         TEXT        NOT NULL,
    payload     JSONB       NOT NULL,
    error       TEXT,
    attempts    INT         NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_tried  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inventory_events_sku     ON inventory_events(sku);
CREATE INDEX IF NOT EXISTS idx_inventory_events_site    ON inventory_events(site_id);
CREATE INDEX IF NOT EXISTS idx_propagation_failures_sku ON propagation_failures(sku);
