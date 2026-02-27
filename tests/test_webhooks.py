"""
Integration tests for webhook endpoints using HTTPX test client.
Tests signature verification and payload handling.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Product, Stock
from app.main import app
from app.database import get_db

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
TEST_SECRET = "testsecret"

# Override settings for tests
import os
os.environ.setdefault("WEBHOOK_SHARED_SECRET", TEST_SECRET)
os.environ.setdefault("SITES", "[]")
os.environ.setdefault("DATABASE_URL", TEST_DB_URL)


@pytest_asyncio.fixture(scope="function")
async def test_db():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        session.add(Product(sku="TEST-SKU", name="Test", backorders=False))
        session.add(Stock(sku="TEST-SKU", on_hand=100, reserved=0))
        await session.commit()

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield factory
    app.dependency_overrides.clear()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def _sign(body: bytes, secret: str = TEST_SECRET) -> str:
    sig = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(sig).decode()


@pytest.mark.asyncio
async def test_order_paid_webhook(test_db):
    payload = {
        "site_id": "site1",
        "order_id": "10",
        "status": "processing",
        "line_items": [{"line_item_id": "1", "sku": "TEST-SKU", "qty": 5}],
    }
    body = json.dumps(payload).encode()
    sig = _sign(body)

    with patch("app.services.propagation.enqueue"), \
         patch("app.services.airtable.write_stock_snapshot", new_callable=AsyncMock), \
         patch("app.services.airtable.write_event", new_callable=AsyncMock):

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/webhooks/woocommerce/order_paid",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-WC-Webhook-Signature": sig,
                },
            )

    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_wrong_signature_rejected(test_db):
    payload = {
        "site_id": "site1",
        "order_id": "20",
        "status": "processing",
        "line_items": [{"line_item_id": "1", "sku": "TEST-SKU", "qty": 1}],
    }
    body = json.dumps(payload).encode()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/webhooks/woocommerce/order_paid",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-WC-Webhook-Signature": "invalidsignature==",
            },
        )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wrong_status_ignored(test_db):
    """Orders with status != decrement_status should return 204 but not mutate stock."""
    payload = {
        "site_id": "site1",
        "order_id": "30",
        "status": "pending",            # not 'processing'
        "line_items": [{"line_item_id": "1", "sku": "TEST-SKU", "qty": 5}],
    }
    body = json.dumps(payload).encode()
    sig = _sign(body)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/webhooks/woocommerce/order_paid",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-WC-Webhook-Signature": sig,
            },
        )

    assert resp.status_code == 204

    # Stock must be unchanged
    async with test_db() as session:
        stock = await session.get(Stock, "TEST-SKU")
        assert stock.on_hand == 100


@pytest.mark.asyncio
async def test_health_endpoint(test_db):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/admin/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
