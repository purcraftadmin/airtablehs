"""
Core inventory service: transactional stock mutations with idempotency.
"""
from __future__ import annotations

import logging
from typing import List, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InventoryEvent, Product, Stock

logger = logging.getLogger(__name__)


async def _ensure_product_and_stock(session: AsyncSession, sku: str) -> None:
    """Upsert a bare product + stock row if not present (allows unknown SKUs)."""
    if not await session.get(Product, sku):
        session.add(Product(sku=sku, name=sku))
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


async def apply_delta(
    session: AsyncSession,
    site_id: str,
    order_id: str,
    line_item_id: str,
    sku: str,
    delta: int,        # negative = decrement, positive = increment
    event_type: str,
) -> Tuple[bool, int]:
    """
    Atomically apply *delta* to stock.on_hand and record an inventory_event.

    Returns (was_new_event: bool, new_on_hand: int).
    Idempotent: duplicate (site_id, order_id, line_item_id, event_type) is a no-op.
    Prevents negative stock unless backorders are enabled for the SKU.
    """
    await _ensure_product_and_stock(session, sku)

    # ── Idempotency check: try INSERT, catch unique-constraint violation ──────
    event = InventoryEvent(
        site_id=site_id,
        order_id=order_id,
        line_item_id=line_item_id,
        sku=sku,
        delta=delta,
        event_type=event_type,
    )
    session.add(event)
    try:
        await session.flush()
    except IntegrityError:
        # Duplicate event – rollback this flush only and return current stock
        await session.rollback()
        stock_row = await session.get(Stock, sku)
        current = stock_row.on_hand if stock_row else 0
        logger.info(
            "Duplicate event skipped: site=%s order=%s item=%s sku=%s type=%s",
            site_id, order_id, line_item_id, sku, event_type,
        )
        return False, current

    # ── Transactional stock update with row-level lock ────────────────────────
    # with_for_update() is PostgreSQL-only; fall back gracefully for SQLite tests.
    try:
        stock_row = (
            await session.execute(
                select(Stock).where(Stock.sku == sku).with_for_update()
            )
        ).scalar_one_or_none()
    except Exception:
        stock_row = (
            await session.execute(select(Stock).where(Stock.sku == sku))
        ).scalar_one_or_none()

    if stock_row is None:
        stock_row = Stock(sku=sku, on_hand=0, reserved=0)
        session.add(stock_row)
        await session.flush()

    # Fetch backorders flag
    product = await session.get(Product, sku)
    backorders_allowed = product.backorders if product else False

    new_on_hand = stock_row.on_hand + delta

    if new_on_hand < 0 and not backorders_allowed:
        logger.warning(
            "Stock floor hit for sku=%s (would go %d -> %d); clamping to 0",
            sku, stock_row.on_hand, new_on_hand,
        )
        new_on_hand = 0

    stock_row.on_hand = new_on_hand
    session.add(stock_row)

    logger.info(
        "Stock updated: sku=%s delta=%+d new_on_hand=%d (site=%s order=%s)",
        sku, delta, new_on_hand, site_id, order_id,
    )

    return True, new_on_hand


async def get_stock(session: AsyncSession, sku: str) -> int:
    """Return current on_hand for a SKU (0 if unknown)."""
    row = await session.get(Stock, sku)
    return row.on_hand if row else 0


async def bulk_apply_deltas(
    session: AsyncSession,
    site_id: str,
    order_id: str,
    line_items: list,
    event_type: str,
) -> List[Tuple[str, bool, int]]:
    """
    Apply deltas for all line items within the current session/transaction.
    Returns [(sku, was_new, new_on_hand), ...]
    sign of delta is determined by event_type: negative for orders, positive for refunds/cancels.
    """
    results = []
    sign = -1 if event_type == "order_paid" else +1

    for item in line_items:
        was_new, on_hand = await apply_delta(
            session=session,
            site_id=site_id,
            order_id=order_id,
            line_item_id=item.line_item_id,
            sku=item.sku,
            delta=sign * item.qty,
            event_type=event_type,
        )
        results.append((item.sku, was_new, on_hand))

    return results
