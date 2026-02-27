"""
Central configuration – loaded once at startup from environment / .env file.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import List, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SiteConfig(BaseSettings):
    """One WooCommerce site entry from the SITES JSON array."""
    site_id: str
    base_url: str        # e.g. https://shop1.example.com
    wc_key: str          # consumer key
    wc_secret: str       # consumer secret

    model_config = SettingsConfigDict(extra="ignore")


class Settings(BaseSettings):
    # ── Database ────────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://wcinv:wcinv@localhost:5432/wcinv"

    # ── Sites ────────────────────────────────────────────────────────────────
    sites_json: str = "[]"          # raw JSON string from env SITES
    sites: List[SiteConfig] = []    # parsed at startup

    # ── Webhook security ─────────────────────────────────────────────────────
    webhook_shared_secret: str = ""
    # If set, also accept Bearer <token> instead of HMAC
    webhook_bearer_token: Optional[str] = None

    # ── Behaviour ────────────────────────────────────────────────────────────
    decrement_status: str = "processing"

    # ── Airtable (optional) ──────────────────────────────────────────────────
    airtable_api_key: Optional[str] = None
    airtable_base_id: Optional[str] = None
    # JSON: {"stock": "tblXXX", "events": "tblYYY"}
    airtable_tables_json: Optional[str] = None

    # ── Worker ──────────────────────────────────────────────────────────────
    propagation_max_retries: int = 5
    propagation_retry_base_seconds: float = 2.0

    # ── Admin UI ─────────────────────────────────────────────────────────────
    # Secret for signing session cookies. Generate: openssl rand -hex 32
    session_secret_key: str = "change-me-in-production-use-openssl-rand-hex-32"
    # Fernet key for encrypting WC credentials. Generate:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    config_encryption_key: str = ""
    bootstrap_admin_user: Optional[str] = None
    bootstrap_admin_password: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="",
        populate_by_name=True,
    )

    @field_validator("sites", mode="before")
    @classmethod
    def parse_sites(cls, v, info):
        # v comes from the field default; read from sites_json instead
        return v

    def model_post_init(self, __context) -> None:
        raw = self.sites_json or os.environ.get("SITES", "[]")
        parsed = json.loads(raw)
        object.__setattr__(self, "sites", [SiteConfig(**s) for s in parsed])

    @property
    def sites_by_id(self) -> dict[str, SiteConfig]:
        return {s.site_id: s for s in self.sites}

    @property
    def airtable_tables(self) -> dict[str, str]:
        if self.airtable_tables_json:
            return json.loads(self.airtable_tables_json)
        return {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
