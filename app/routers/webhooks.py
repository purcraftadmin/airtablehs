"""
WooCommerce webhook receivers.

POST /webhooks/woocommerce/order_paid
POST /webhooks/woocommerce/refund_or_cancel
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.deps import verify_webhook
from app.schemas import OrderWebhookPayload, RefundCancelPayload
from app.services import airtable, propagation
from app.services.inventory import bulk_apply_deltas, get_stock

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/webhooks/woocommerce", tags=["webhooks"])


@router.post("/order_paid", status_code=status.HTTP_204_NO_CONTENT)
async def order_paid(
    request: Request,
    body: bytes = Depends(verify_webhook),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Receive an order event.  Decrements stock for each line item and propagates.
    Idempotent: re-delivering the same event is safe.
    """
    import json
    payload = OrderWebhookPayload(**json.loads(body))

    # Guard: only decrement for the configured status
    if payload.status.lower() != settings.decrement_status.lower():
        logger.debug(
            "Ignored order %s on site %s with status=%s (want %s)",
            payload.order_id, payload.site_id, payload.status, settings.decrement_status,
        )
        return

    if not payload.line_items:
        return

    results = await bulk_apply_deltas(
        session=db,
        site_id=payload.site_id,
        order_id=payload.order_id,
        line_items=payload.line_items,
        event_type="order_paid",
    )

    # Propagate and (optionally) write to Airtable for each changed SKU
    for sku, was_new, new_on_hand in results:
        if was_new:
            propagation.enqueue(sku, new_on_hand)
            # Airtable writes are fire-and-forget
            import asyncio
            asyncio.create_task(
                airtable.write_stock_snapshot(sku=sku, on_hand=new_on_hand)
            )
            asyncio.create_task(
                airtable.write_event(
                    site_id=payload.site_id,
                    order_id=payload.order_id,
                    sku=sku,
                    delta=-(next(i.qty for i in payload.line_items if i.sku == sku)),
                    event_type="order_paid",
                    new_on_hand=new_on_hand,
                )
            )


@router.post("/refund_or_cancel", status_code=status.HTTP_204_NO_CONTENT)
async def refund_or_cancel(
    request: Request,
    body: bytes = Depends(verify_webhook),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Receive a refund or cancellation.  Increments stock (+qty) and propagates.
    Idempotent: re-delivering the same event is safe.
    """
    import json
    payload = RefundCancelPayload(**json.loads(body))

    if payload.event_type not in ("refund", "cancel"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid event_type: {payload.event_type}",
        )

    if not payload.line_items:
        return

    results = await bulk_apply_deltas(
        session=db,
        site_id=payload.site_id,
        order_id=payload.order_id,
        line_items=payload.line_items,
        event_type=payload.event_type,
    )

    for sku, was_new, new_on_hand in results:
        if was_new:
            propagation.enqueue(sku, new_on_hand)
            import asyncio
            asyncio.create_task(
                airtable.write_stock_snapshot(sku=sku, on_hand=new_on_hand)
            )
            asyncio.create_task(
                airtable.write_event(
                    site_id=payload.site_id,
                    order_id=payload.order_id,
                    sku=sku,
                    delta=+(next(i.qty for i in payload.line_items if i.sku == sku)),
                    event_type=payload.event_type,
                    new_on_hand=new_on_hand,
                )
            )
