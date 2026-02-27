"""
Tests for admin authentication: login, logout, access protection.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Configure test env before app import
os.environ.setdefault("CONFIG_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("SESSION_SECRET_KEY", "test-session-secret-32chars-xxxxx")
os.environ.setdefault("SITES", "[]")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.models import AdminUser, Base  # noqa: E402
from app.main import app                # noqa: E402
from app.database import get_db         # noqa: E402
from app.admin.auth import hash_password  # noqa: E402

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function")
async def test_db():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        session.add(AdminUser(
            username="admin",
            password_hash=hash_password("secret123"),
            is_active=True,
        ))
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


@pytest.mark.asyncio
async def test_unauthenticated_redirect_to_login(test_db):
    """GET /admin without session should redirect to /admin/login."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/admin", follow_redirects=False)

    assert resp.status_code == 303
    assert "/admin/login" in resp.headers["location"]


@pytest.mark.asyncio
async def test_login_page_accessible(test_db):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/admin/login")

    assert resp.status_code == 200
    assert b"Sign In" in resp.content


@pytest.mark.asyncio
async def test_login_valid_credentials(test_db):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        follow_redirects=False,
    ) as client:
        resp = await client.post(
            "/admin/login",
            data={"username": "admin", "password": "secret123"},
        )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin"


@pytest.mark.asyncio
async def test_login_invalid_password(test_db):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        follow_redirects=False,
    ) as client:
        resp = await client.post(
            "/admin/login",
            data={"username": "admin", "password": "wrongpassword"},
        )

    # Should redirect back to login
    assert resp.status_code == 303
    assert "/admin/login" in resp.headers["location"]


@pytest.mark.asyncio
async def test_login_unknown_user(test_db):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        follow_redirects=False,
    ) as client:
        resp = await client.post(
            "/admin/login",
            data={"username": "nobody", "password": "anything"},
        )

    assert resp.status_code == 303
    assert "/admin/login" in resp.headers["location"]


@pytest.mark.asyncio
async def test_protected_routes_require_auth(test_db):
    """All protected admin routes should redirect to login when unauthenticated."""
    protected = ["/admin", "/admin/sites", "/admin/settings", "/admin/audit"]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        follow_redirects=False,
    ) as client:
        for path in protected:
            resp = await client.get(path)
            assert resp.status_code == 303, f"{path} should redirect"
            assert "/admin/login" in resp.headers["location"]


@pytest.mark.asyncio
async def test_logout_clears_session(test_db):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        follow_redirects=False,
    ) as client:
        # Login
        await client.post(
            "/admin/login",
            data={"username": "admin", "password": "secret123"},
        )
        # Logout
        resp = await client.post("/admin/logout")
        assert resp.status_code == 303

        # Should be back to login-redirect on protected route
        resp2 = await client.get("/admin")
        assert resp2.status_code == 303
        assert "/admin/login" in resp2.headers["location"]
