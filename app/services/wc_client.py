"""
Thin WooCommerce REST API client (no SDK dependency).
Uses HTTP Basic auth with consumer key/secret.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import SiteConfig

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0)


def _auth(site: SiteConfig) -> httpx.BasicAuth:
    return httpx.BasicAuth(site.wc_key, site.wc_secret)


def _base(site: SiteConfig) -> str:
    return site.base_url.rstrip("/") + "/wp-json/wc/v3"


async def set_product_stock(
    site: SiteConfig, product_id: int, stock_quantity: int
) -> bool:
    """Update stock_quantity on a simple product."""
    url = f"{_base(site)}/products/{product_id}"
    payload = {"manage_stock": True, "stock_quantity": stock_quantity}
    async with httpx.AsyncClient(auth=_auth(site), timeout=_TIMEOUT) as client:
        resp = await client.put(url, json=payload)
        if resp.is_success:
            return True
        logger.error(
            "WC API error site=%s product=%d status=%d body=%s",
            site.site_id, product_id, resp.status_code, resp.text[:300],
        )
        return False


async def set_variation_stock(
    site: SiteConfig, product_id: int, variation_id: int, stock_quantity: int
) -> bool:
    """Update stock_quantity on a product variation."""
    url = f"{_base(site)}/products/{product_id}/variations/{variation_id}"
    payload = {"manage_stock": True, "stock_quantity": stock_quantity}
    async with httpx.AsyncClient(auth=_auth(site), timeout=_TIMEOUT) as client:
        resp = await client.put(url, json=payload)
        if resp.is_success:
            return True
        logger.error(
            "WC API error site=%s product=%d variation=%d status=%d body=%s",
            site.site_id, product_id, variation_id, resp.status_code, resp.text[:300],
        )
        return False


async def fetch_all_products(site: SiteConfig) -> List[Dict[str, Any]]:
    """Paginate through all WooCommerce products."""
    products: List[Dict[str, Any]] = []
    page = 1
    async with httpx.AsyncClient(auth=_auth(site), timeout=_TIMEOUT) as client:
        while True:
            url = f"{_base(site)}/products"
            resp = await client.get(url, params={"per_page": 100, "page": page})
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            products.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    return products


async def fetch_variations(
    site: SiteConfig, product_id: int
) -> List[Dict[str, Any]]:
    """Fetch all variations for a variable product."""
    variations: List[Dict[str, Any]] = []
    page = 1
    async with httpx.AsyncClient(auth=_auth(site), timeout=_TIMEOUT) as client:
        while True:
            url = f"{_base(site)}/products/{product_id}/variations"
            resp = await client.get(url, params={"per_page": 100, "page": page})
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            variations.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    return variations
