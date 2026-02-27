"""
Optional Airtable writer – snapshots and recent transactions.
Only active when AIRTABLE_API_KEY / AIRTABLE_BASE_ID are configured.
Does NOT serve as SSOT; writes are best-effort and non-blocking.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_AIRTABLE_BASE = "https://api.airtable.com/v0"
_TIMEOUT = httpx.Timeout(20.0)


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {settings.airtable_api_key}"}


def _is_configured() -> bool:
    return bool(settings.airtable_api_key and settings.airtable_base_id)


async def _upsert_records(
    table_id: str,
    records: List[Dict[str, Any]],
    merge_on: List[str],
) -> None:
    """
    Upsert records into an Airtable table using the PATCH upsert endpoint.
    Batches in groups of 10 (Airtable limit).
    """
    if not _is_configured():
        return

    url = f"{_AIRTABLE_BASE}/{settings.airtable_base_id}/{table_id}"
    headers = _headers()

    for i in range(0, len(records), 10):
        batch = records[i : i + 10]
        payload = {
            "performUpsert": {"fieldsToMergeOn": merge_on},
            "records": [{"fields": r} for r in batch],
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.patch(url, json=payload, headers=headers)
            if not resp.is_success:
                logger.error(
                    "Airtable upsert failed table=%s status=%d body=%s",
                    table_id, resp.status_code, resp.text[:300],
                )


async def write_stock_snapshot(
    sku: str,
    on_hand: int,
    avg_7d: Optional[float] = None,
    avg_30d: Optional[float] = None,
    last_50_summary: Optional[str] = None,
) -> None:
    """Upsert a stock snapshot row in the Airtable stock table."""
    tables = settings.airtable_tables
    table_id = tables.get("stock")
    if not table_id:
        return

    record: Dict[str, Any] = {
        "SKU": sku,
        "On Hand": on_hand,
        "Updated At": datetime.now(timezone.utc).isoformat(),
    }
    if avg_7d is not None:
        record["7d Avg Daily Sales"] = round(avg_7d, 2)
    if avg_30d is not None:
        record["30d Avg Daily Sales"] = round(avg_30d, 2)
    if last_50_summary is not None:
        record["Last 50 Txn Summary"] = last_50_summary

    await _upsert_records(table_id, [record], merge_on=["SKU"])


async def write_event(
    site_id: str,
    order_id: str,
    sku: str,
    delta: int,
    event_type: str,
    new_on_hand: int,
) -> None:
    """Append a transaction row in the Airtable events table."""
    tables = settings.airtable_tables
    table_id = tables.get("events")
    if not table_id:
        return

    record: Dict[str, Any] = {
        "Site": site_id,
        "Order ID": order_id,
        "SKU": sku,
        "Delta": delta,
        "Event Type": event_type,
        "On Hand After": new_on_hand,
        "Timestamp": datetime.now(timezone.utc).isoformat(),
    }
    # Events table uses auto-ID; no merge key – just create
    url = f"{_AIRTABLE_BASE}/{settings.airtable_base_id}/{table_id}"
    headers = _headers()
    if not _is_configured():
        return
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            url,
            json={"records": [{"fields": record}]},
            headers=headers,
        )
        if not resp.is_success:
            logger.error(
                "Airtable event write failed status=%d body=%s",
                resp.status_code, resp.text[:300],
            )
