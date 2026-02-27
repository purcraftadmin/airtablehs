"""
Unit tests for stock decrement / increment logic.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from app.models import Product, Stock
from app.services.inventory import apply_delta, get_stock


@pytest_asyncio.fixture(autouse=True)
async def seed_product(db_session):
    """Pre-insert a product + stock row for use in tests."""
    db_session.add(Product(sku="WIDGET-1", name="Widget 1", backorders=False))
    db_session.add(Stock(sku="WIDGET-1", on_hand=100, reserved=0))
    db_session.add(Product(sku="BACKORDER-1", name="Backorder Item", backorders=True))
    db_session.add(Stock(sku="BACKORDER-1", on_hand=5, reserved=0))
    await db_session.commit()


@pytest.mark.asyncio
async def test_decrement_reduces_stock(db_session):
    was_new, new_on_hand = await apply_delta(
        session=db_session,
        site_id="site1",
        order_id="100",
        line_item_id="1",
        sku="WIDGET-1",
        delta=-10,
        event_type="order_paid",
    )
    await db_session.commit()

    assert was_new is True
    assert new_on_hand == 90
    assert await get_stock(db_session, "WIDGET-1") == 90


@pytest.mark.asyncio
async def test_increment_increases_stock(db_session):
    # First decrement
    await apply_delta(
        session=db_session,
        site_id="site1",
        order_id="200",
        line_item_id="1",
        sku="WIDGET-1",
        delta=-20,
        event_type="order_paid",
    )
    # Then refund
    was_new, new_on_hand = await apply_delta(
        session=db_session,
        site_id="site1",
        order_id="200",
        line_item_id="1",
        sku="WIDGET-1",
        delta=+20,
        event_type="refund",
    )
    await db_session.commit()

    assert was_new is True
    assert new_on_hand == 100


@pytest.mark.asyncio
async def test_stock_floor_without_backorders(db_session):
    """Stock must not go negative when backorders=False."""
    _, new_on_hand = await apply_delta(
        session=db_session,
        site_id="site1",
        order_id="300",
        line_item_id="1",
        sku="WIDGET-1",
        delta=-9999,   # more than available
        event_type="order_paid",
    )
    await db_session.commit()

    assert new_on_hand == 0
    assert await get_stock(db_session, "WIDGET-1") == 0


@pytest.mark.asyncio
async def test_backorders_allow_negative(db_session):
    """Stock CAN go negative when backorders=True."""
    _, new_on_hand = await apply_delta(
        session=db_session,
        site_id="site1",
        order_id="400",
        line_item_id="1",
        sku="BACKORDER-1",
        delta=-10,   # only 5 in stock
        event_type="order_paid",
    )
    await db_session.commit()

    assert new_on_hand == -5


@pytest.mark.asyncio
async def test_unknown_sku_auto_created(db_session):
    """Applying delta to an unknown SKU should auto-create product + stock."""
    was_new, new_on_hand = await apply_delta(
        session=db_session,
        site_id="site1",
        order_id="500",
        line_item_id="1",
        sku="BRAND-NEW-SKU",
        delta=-3,
        event_type="order_paid",
    )
    await db_session.commit()

    # Stock starts at 0 and backorders=False, so clamped to 0
    assert was_new is True
    assert new_on_hand == 0
