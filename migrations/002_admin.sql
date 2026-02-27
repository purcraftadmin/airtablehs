-- Migration 002: Admin UI tables
-- Run after migrations/init.sql

CREATE TABLE IF NOT EXISTS admin_users (
    id            TEXT        PRIMARY KEY,
    username      TEXT        NOT NULL UNIQUE,
    password_hash TEXT        NOT NULL,
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Single-row application settings (always id=1)
CREATE TABLE IF NOT EXISTS app_settings (
    id                       INT         PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    decrement_status         TEXT        NOT NULL DEFAULT 'processing',
    backorders_default       BOOLEAN     NOT NULL DEFAULT FALSE,
    webhook_auth_mode        TEXT        NOT NULL DEFAULT 'hmac',  -- 'hmac' | 'bearer'
    airtable_enabled         BOOLEAN     NOT NULL DEFAULT FALSE,
    airtable_base_id         TEXT,
    airtable_table_names     TEXT,       -- JSON string {"stock":"tblXXX","events":"tblYYY"}
    airtable_api_key_encrypted TEXT,
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- WooCommerce sites managed through the admin UI
CREATE TABLE IF NOT EXISTS sites (
    id               TEXT        PRIMARY KEY,
    site_id          TEXT        NOT NULL UNIQUE,
    name             TEXT        NOT NULL DEFAULT '',
    base_url         TEXT        NOT NULL,
    wc_key_encrypted TEXT        NOT NULL,
    wc_secret_encrypted TEXT     NOT NULL,
    is_active        BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_sync_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_sites_site_id    ON sites(site_id);
CREATE INDEX IF NOT EXISTS idx_sites_is_active  ON sites(is_active);
CREATE INDEX IF NOT EXISTS idx_admin_users_name ON admin_users(username);
