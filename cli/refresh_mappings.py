#!/usr/bin/env python3
"""
CLI: Refresh SKU -> (product_id, variation_id) mappings from WooCommerce.

Usage:
    # Refresh all configured sites
    python -m cli.refresh_mappings

    # Refresh a single site
    python -m cli.refresh_mappings --site site1

    # Print current mappings
    python -m cli.refresh_mappings --list

    # Show stock levels
    python -m cli.refresh_mappings --stock
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, text

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import SiteSkuMap, Stock
from app.services.mapping import refresh_site_mappings


async def cmd_refresh(site_id: str | None) -> None:
    settings = get_settings()
    sites = settings.sites

    if site_id:
        sites = [s for s in sites if s.site_id == site_id]
        if not sites:
            print(f"ERROR: site_id {site_id!r} not found in SITES config", file=sys.stderr)
            sys.exit(1)

    if not sites:
        print("ERROR: No sites configured. Check your SITES env variable.", file=sys.stderr)
        sys.exit(1)

    async with AsyncSessionLocal() as session:
        for site in sites:
            print(f"\n→ Refreshing mappings for site: {site.site_id} ({site.base_url})")
            result = await refresh_site_mappings(session, site)
            print(f"  Mapped:  {result.inserted}")
            if result.errors:
                print(f"  Errors:  {len(result.errors)}")
                for e in result.errors[:10]:
                    print(f"    - {e}")


async def cmd_list(site_id: str | None) -> None:
    async with AsyncSessionLocal() as session:
        stmt = select(SiteSkuMap).order_by(SiteSkuMap.site_id, SiteSkuMap.sku)
        if site_id:
            stmt = stmt.where(SiteSkuMap.site_id == site_id)
        rows = (await session.execute(stmt)).scalars().all()

    if not rows:
        print("No mappings found. Run without --list to refresh.")
        return

    print(f"\n{'SITE':<20} {'SKU':<30} {'PRODUCT_ID':<12} {'VARIATION_ID':<14} REFRESHED")
    print("-" * 100)
    for r in rows:
        var = str(r.variation_id) if r.variation_id else "-"
        print(f"{r.site_id:<20} {r.sku:<30} {r.product_id:<12} {var:<14} {r.refreshed_at}")


async def cmd_stock() -> None:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(Stock).order_by(Stock.sku))).scalars().all()

    if not rows:
        print("No stock records found.")
        return

    print(f"\n{'SKU':<30} {'ON_HAND':>10} {'RESERVED':>10} UPDATED")
    print("-" * 80)
    for r in rows:
        print(f"{r.sku:<30} {r.on_hand:>10} {r.reserved:>10} {r.updated_at}")


def main() -> None:
    parser = argparse.ArgumentParser(description="WC Inventory Sync CLI")
    parser.add_argument("--site", metavar="SITE_ID", help="Target a single site")
    parser.add_argument("--list", action="store_true", help="Print current SKU mappings")
    parser.add_argument("--stock", action="store_true", help="Print current stock levels")
    args = parser.parse_args()

    if args.list:
        asyncio.run(cmd_list(args.site))
    elif args.stock:
        asyncio.run(cmd_stock())
    else:
        asyncio.run(cmd_refresh(args.site))


if __name__ == "__main__":
    main()
