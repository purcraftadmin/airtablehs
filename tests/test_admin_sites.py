"""
Tests for admin site management: add, edit, deactivate.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Configure test env before app import
_TEST_KEY = Fernet.generate_key().decode()
os.environ["CONFIG_ENCRYPTION_KEY"] = _TEST_KEY
os.environ.setdefault("SESSION_SECRET_KEY", "test-session-secret-32chars-xxxxx")
os.environ.setdefault("SITES", "[]")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import app.admin.crypto as crypto_mod  # noqa: E402
crypto_mod._fernet = None              # reset so test key is picked up

from app.models import AdminUser, Base, Site  # noqa: E402
from app.main import app                      # noqa: E402
from app.database import get_db               # noqa: E402
from app.admin.auth import hash_password, decrypt  # noqa: E402 (not a crypto decrypt)
from app.admin.crypto import decrypt as crypto_decrypt  # noqa: E402

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function")
async def client_and_db():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        session.add(AdminUser(
            username="admin",
            password_hash=hash_password("pass"),
            is_active=True,
        ))
        await session.commit()

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    crypto_mod._fernet = None  # ensure fresh Fernet with test key

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        # Authenticate
        await client.post("/admin/login", data={"username": "admin", "password": "pass"})
        yield client, factory

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    crypto_mod._fernet = None


@pytest.mark.asyncio
async def test_add_site(client_and_db):
    client, factory = client_and_db

    resp = await client.post("/admin/sites", data={
        "name": "Test Shop",
        "site_id": "testshop",
        "base_url": "https://testshop.example.com",
        "wc_key": "ck_test123",
        "wc_secret": "cs_test456",
        "is_active": "true",
    })
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/sites"

    # Verify persisted
    async with factory() as session:
        site = (
            await session.execute(select(Site).where(Site.site_id == "testshop"))
        ).scalar_one_or_none()

    assert site is not None
    assert site.name == "Test Shop"
    assert site.is_active is True
    # Credentials are encrypted, not stored in plaintext
    assert site.wc_key_encrypted != "ck_test123"
    assert crypto_decrypt(site.wc_key_encrypted) == "ck_test123"


@pytest.mark.asyncio
async def test_add_site_duplicate_site_id(client_and_db):
    client, factory = client_and_db

    data = {
        "name": "Shop A", "site_id": "dupshop",
        "base_url": "https://a.example.com",
        "wc_key": "ck_a", "wc_secret": "cs_a",
    }
    resp1 = await client.post("/admin/sites", data=data)
    assert resp1.status_code == 303

    # Second post with same site_id should fail with 422
    resp2 = await client.post("/admin/sites", data=data)
    assert resp2.status_code == 422


@pytest.mark.asyncio
async def test_edit_site(client_and_db):
    client, factory = client_and_db

    # Create
    await client.post("/admin/sites", data={
        "name": "Original", "site_id": "editme",
        "base_url": "https://original.example.com",
        "wc_key": "ck_orig", "wc_secret": "cs_orig",
    })

    async with factory() as session:
        site = (
            await session.execute(select(Site).where(Site.site_id == "editme"))
        ).scalar_one()
    site_uuid = site.id

    # Edit
    resp = await client.post(f"/admin/sites/{site_uuid}", data={
        "name": "Updated Name",
        "base_url": "https://updated.example.com",
        "wc_key": "",        # keep existing
        "wc_secret": "",     # keep existing
    })
    assert resp.status_code == 303

    async with factory() as session:
        updated = await session.get(Site, site_uuid)

    assert updated.name == "Updated Name"
    assert updated.base_url == "https://updated.example.com"
    # Credentials unchanged (empty submission = keep existing)
    assert crypto_decrypt(updated.wc_key_encrypted) == "ck_orig"


@pytest.mark.asyncio
async def test_deactivate_site(client_and_db):
    client, factory = client_and_db

    await client.post("/admin/sites", data={
        "name": "Shop D", "site_id": "deactivateme",
        "base_url": "https://d.example.com",
        "wc_key": "ck_d", "wc_secret": "cs_d",
        "is_active": "true",
    })

    async with factory() as session:
        site = (
            await session.execute(select(Site).where(Site.site_id == "deactivateme"))
        ).scalar_one()
    assert site.is_active is True

    resp = await client.post(f"/admin/sites/{site.id}/deactivate")
    assert resp.status_code == 303

    async with factory() as session:
        refreshed = await session.get(Site, site.id)
    assert refreshed.is_active is False


@pytest.mark.asyncio
async def test_sites_list_page(client_and_db):
    client, _ = client_and_db

    resp = await client.get("/admin/sites")
    assert resp.status_code == 200
    assert b"WooCommerce Sites" in resp.content or b"Sites" in resp.content


@pytest.mark.asyncio
async def test_new_site_form(client_and_db):
    client, _ = client_and_db

    resp = await client.get("/admin/sites/new")
    assert resp.status_code == 200
    assert b"Add Site" in resp.content
