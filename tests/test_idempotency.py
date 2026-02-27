"""
Unit tests for event idempotency – re-delivering the same webhook must be a no-op.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from app.models import Product, Stock
from app.services.inventory import apply_delta, get_stock


@pytest_asyncio.fixture(autouse=True)
async def seed(db_session):
    db_session.add(Product(sku="IDEMPOTENT-SKU", name="Test", backorders=False))
    db_session.add(Stock(sku="IDEMPOTENT-SKU", on_hand=50, reserved=0))
    await db_session.commit()


@pytest.mark.asyncio
async def test_duplicate_order_event_is_noop(db_session):
    """
    Delivering the same (site_id, order_id, line_item_id, event_type) twice
    must only decrement stock once.
    """
    kwargs = dict(
        session=db_session,
        site_id="site1",
        order_id="ORD-001",
        line_item_id="LI-1",
        sku="IDEMPOTENT-SKU",
        delta=-5,
        event_type="order_paid",
    )

    was_new_1, on_hand_1 = await apply_delta(**kwargs)
    await db_session.commit()

    was_new_2, on_hand_2 = await apply_delta(**kwargs)
    await db_session.commit()

    assert was_new_1 is True
    assert was_new_2 is False          # duplicate detected
    assert on_hand_1 == on_hand_2      # stock unchanged on duplicate
    assert on_hand_1 == 45             # only decremented once


@pytest.mark.asyncio
async def test_different_event_types_are_independent(db_session):
    """
    order_paid and refund for the same (site, order, line_item) are separate events.
    """
    base = dict(
        session=db_session,
        site_id="site1",
        order_id="ORD-002",
        line_item_id="LI-1",
        sku="IDEMPOTENT-SKU",
        delta=-5,
    )

    was_new_1, _ = await apply_delta(**base, event_type="order_paid")
    was_new_2, _ = await apply_delta(**base, delta=+5, event_type="refund")
    await db_session.commit()

    assert was_new_1 is True
    assert was_new_2 is True
    # Net delta = -5 + 5 = 0 → back to 50
    assert await get_stock(db_session, "IDEMPOTENT-SKU") == 50


@pytest.mark.asyncio
async def test_duplicate_refund_is_noop(db_session):
    """Duplicate refund should not increase stock twice."""
    kwargs = dict(
        session=db_session,
        site_id="site2",
        order_id="ORD-003",
        line_item_id="LI-2",
        sku="IDEMPOTENT-SKU",
        delta=+10,
        event_type="refund",
    )

    was_new_1, on_hand_1 = await apply_delta(**kwargs)
    await db_session.commit()
    was_new_2, on_hand_2 = await apply_delta(**kwargs)
    await db_session.commit()

    assert was_new_1 is True
    assert was_new_2 is False
    assert on_hand_1 == on_hand_2      # stock not doubled
    assert on_hand_1 == 60             # 50 + 10


@pytest.mark.asyncio
async def test_separate_line_items_are_independent(db_session):
    """Different line_item_ids within the same order are separate events."""
    base = dict(
        session=db_session,
        site_id="site1",
        order_id="ORD-004",
        sku="IDEMPOTENT-SKU",
        delta=-3,
        event_type="order_paid",
    )

    was_new_1, _ = await apply_delta(**base, line_item_id="LI-A")
    was_new_2, on_hand_2 = await apply_delta(**base, line_item_id="LI-B")
    await db_session.commit()

    assert was_new_1 is True
    assert was_new_2 is True
    # Two distinct line items → total -6
    assert on_hand_2 == 44
