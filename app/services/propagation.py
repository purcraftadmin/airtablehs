"""
Stock propagation: push SSOT stock_quantity to every WooCommerce site.

Architecture:
  - An asyncio.Queue receives PropagationJob items.
  - A background worker coroutine drains the queue with retries + dead-letter logging.
  - Callers enqueue a job after a successful inventory transaction.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List

from sqlalchemy import select

from app.config import SiteConfig, get_settings
from app.database import get_db_ctx
from app.models import PropagationFailure, SiteSkuMap
from app.services.wc_client import set_product_stock, set_variation_stock

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Job definition ───────────────────────────────────────────────────────────

@dataclass
class PropagationJob:
    sku: str
    stock_quantity: int
    enqueued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Singleton queue ──────────────────────────────────────────────────────────

_queue: asyncio.Queue[PropagationJob] = asyncio.Queue(maxsize=10_000)


def enqueue(sku: str, stock_quantity: int) -> None:
    """Non-blocking enqueue. Drops job and logs if queue is full."""
    job = PropagationJob(sku=sku, stock_quantity=stock_quantity)
    try:
        _queue.put_nowait(job)
    except asyncio.QueueFull:
        logger.error("Propagation queue full – dropping job for sku=%s", sku)


# ── Worker ───────────────────────────────────────────────────────────────────

async def _propagate_one(site: SiteConfig, job: PropagationJob) -> bool:
    """
    Push stock_quantity to a single WC site for the given SKU.
    Returns True on success, False on failure.
    """
    async with get_db_ctx() as session:
        mapping = await session.get(SiteSkuMap, (site.site_id, job.sku))

    if mapping is None:
        logger.warning(
            "No SKU mapping for site=%s sku=%s – skipping propagation",
            site.site_id, job.sku,
        )
        return True  # not a retriable error

    if mapping.variation_id:
        success = await set_variation_stock(
            site, mapping.product_id, mapping.variation_id, job.stock_quantity
        )
    else:
        success = await set_product_stock(site, mapping.product_id, job.stock_quantity)

    return success


async def _record_failure(
    site_id: str, sku: str, payload: dict, error: str, attempts: int
) -> None:
    async with get_db_ctx() as session:
        now = datetime.now(timezone.utc)
        # Upsert failure record
        existing = (
            await session.execute(
                select(PropagationFailure).where(
                    PropagationFailure.site_id == site_id,
                    PropagationFailure.sku == sku,
                )
            )
        ).scalar_one_or_none()

        if existing:
            existing.error = error
            existing.attempts = attempts
            existing.last_tried = now
            session.add(existing)
        else:
            session.add(
                PropagationFailure(
                    site_id=site_id,
                    sku=sku,
                    payload=payload,
                    error=error,
                    attempts=attempts,
                    created_at=now,
                    last_tried=now,
                )
            )


async def _handle_job(job: PropagationJob) -> None:
    sites = settings.sites
    max_retries = settings.propagation_max_retries
    base_delay = settings.propagation_retry_base_seconds

    for site in sites:
        payload = {"sku": job.sku, "stock_quantity": job.stock_quantity}
        last_error = ""
        success = False

        for attempt in range(1, max_retries + 1):
            try:
                success = await _propagate_one(site, job)
                if success:
                    logger.debug(
                        "Propagated sku=%s qty=%d -> site=%s (attempt %d)",
                        job.sku, job.stock_quantity, site.site_id, attempt,
                    )
                    break
                last_error = "WC API returned non-success"
            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "Propagation error site=%s sku=%s attempt=%d/%d: %s",
                    site.site_id, job.sku, attempt, max_retries, exc,
                )

            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))
                await asyncio.sleep(delay)

        if not success:
            logger.error(
                "Propagation failed after %d attempts for site=%s sku=%s",
                max_retries, site.site_id, job.sku,
            )
            await _record_failure(
                site_id=site.site_id,
                sku=job.sku,
                payload=payload,
                error=last_error,
                attempts=max_retries,
            )


async def worker() -> None:
    """
    Runs as a long-lived background task.
    Drains the propagation queue and handles each job.
    """
    logger.info("Propagation worker started")
    while True:
        job = await _queue.get()
        try:
            await _handle_job(job)
        except Exception as exc:
            logger.exception("Unexpected error in propagation worker: %s", exc)
        finally:
            _queue.task_done()
