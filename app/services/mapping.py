"""
SKU -> (product_id, variation_id) mapping refresh.
Queries WooCommerce REST API and upserts into site_sku_map.
Used by both the admin endpoint and the CLI.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import SiteConfig
from app.models import Product, SiteSkuMap, Stock
from app.schemas import MappingRefreshResult
from app.services.wc_client import fetch_all_products, fetch_variations

logger = logging.getLogger(__name__)


async def _upsert_product(session: AsyncSession, sku: str, name: str) -> None:
    existing = await session.get(Product, sku)
    if existing:
        existing.name = name
        session.add(existing)
    else:
        session.add(Product(sku=sku, name=name))
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            return
    if not await session.get(Stock, sku):
        session.add(Stock(sku=sku, on_hand=0, reserved=0))
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()


async def _upsert_mapping(
    session: AsyncSession,
    site_id: str,
    sku: str,
    product_id: int,
    variation_id: int | None,
) -> None:
    now = datetime.now(timezone.utc)
    existing = await session.get(SiteSkuMap, (site_id, sku))
    if existing:
        existing.product_id = product_id
        existing.variation_id = variation_id
        existing.refreshed_at = now
        session.add(existing)
    else:
        session.add(
            SiteSkuMap(
                site_id=site_id,
                sku=sku,
                product_id=product_id,
                variation_id=variation_id,
                refreshed_at=now,
            )
        )
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()


async def refresh_site_mappings(
    session: AsyncSession, site: SiteConfig
) -> MappingRefreshResult:
    """
    Fetch all products (and variations) from a WC site and upsert mappings.
    Returns a summary of what happened.
    """
    inserted = 0
    errors: List[str] = []

    try:
        products = await fetch_all_products(site)
    except Exception as exc:
        msg = f"Failed to fetch products from {site.site_id}: {exc}"
        logger.error(msg)
        return MappingRefreshResult(site_id=site.site_id, inserted=0, updated=0, errors=[msg])

    for product in products:
        product_id: int = product["id"]
        ptype: str = product.get("type", "simple")
        sku: str = product.get("sku", "").strip()
        name: str = product.get("name", "")

        if ptype == "variable":
            # Fetch variations; each variation may have its own SKU
            try:
                variations = await fetch_variations(site, product_id)
            except Exception as exc:
                errors.append(f"product {product_id}: {exc}")
                continue

            for var in variations:
                var_sku: str = var.get("sku", "").strip()
                var_id: int = var["id"]
                if not var_sku:
                    continue
                try:
                    await _upsert_product(session, var_sku, name or var_sku)
                    await _upsert_mapping(session, site.site_id, var_sku, product_id, var_id)
                    inserted += 1
                except Exception as exc:
                    errors.append(f"variation {var_id} sku={var_sku}: {exc}")
        else:
            if not sku:
                continue
            try:
                await _upsert_product(session, sku, name or sku)
                await _upsert_mapping(session, site.site_id, sku, product_id, None)
                inserted += 1
            except Exception as exc:
                errors.append(f"product {product_id} sku={sku}: {exc}")

    await session.commit()

    logger.info(
        "Mapping refresh site=%s: %d mapped, %d errors",
        site.site_id, inserted, len(errors),
    )
    return MappingRefreshResult(
        site_id=site.site_id,
        inserted=inserted,
        updated=0,
        errors=errors,
    )
