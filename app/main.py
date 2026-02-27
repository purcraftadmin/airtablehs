"""
WooCommerce Shared Inventory Sync Service – FastAPI entry point.
"""
from __future__ import annotations

import asyncio
import logging
import pathlib

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.routers import admin as api_admin, webhooks
from app.admin.deps import AdminNotAuthenticated
from app.admin.routers import auth_routes, dashboard, sites, settings_routes, audit
from app.services import propagation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s – %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(
    title="WC Shared Inventory Sync",
    version="1.0.0",
    description="Multi-site WooCommerce SSOT inventory with propagation and admin UI.",
)

# ── Middleware ────────────────────────────────────────────────────────────────

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret_key,
    session_cookie="inv_session",
    https_only=False,   # set to True behind TLS in production
    same_site="lax",
    max_age=86400 * 7,  # 7 days
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static files ──────────────────────────────────────────────────────────────

_static_dir = pathlib.Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# ── Exception handlers ────────────────────────────────────────────────────────

@app.exception_handler(AdminNotAuthenticated)
async def _admin_not_authenticated(request: Request, exc: AdminNotAuthenticated):
    return RedirectResponse(url="/admin/login", status_code=303)

# ── Routers ───────────────────────────────────────────────────────────────────

# Existing JSON API routes
app.include_router(webhooks.router)
app.include_router(api_admin.router)

# Admin UI routes (HTML + session auth)
app.include_router(auth_routes.router)
app.include_router(dashboard.router)
app.include_router(sites.router)
app.include_router(settings_routes.router)
app.include_router(audit.router)


# ── Startup / shutdown ────────────────────────────────────────────────────────

@app.on_event("startup")
async def _startup() -> None:
    await _bootstrap_admin()
    await _seed_sites_from_env()

    logger.info("Starting propagation worker …")
    asyncio.create_task(propagation.worker(), name="propagation-worker")
    logger.info("Inventory sync service ready.")


@app.on_event("shutdown")
async def _shutdown() -> None:
    logger.info("Draining propagation queue …")
    try:
        await asyncio.wait_for(propagation._queue.join(), timeout=30)
    except asyncio.TimeoutError:
        logger.warning("Propagation queue did not drain within 30 s")


# ── Bootstrap helpers ─────────────────────────────────────────────────────────

async def _bootstrap_admin() -> None:
    """Create the first admin user from env vars if no admin_users exist."""
    username = settings.bootstrap_admin_user
    password = settings.bootstrap_admin_password
    if not username or not password:
        return

    from sqlalchemy import select, func
    from app.database import AsyncSessionLocal
    from app.models import AdminUser
    from app.admin.auth import hash_password

    async with AsyncSessionLocal() as session:
        try:
            count = (
                await session.execute(select(func.count()).select_from(AdminUser))
            ).scalar_one()
            if count == 0:
                session.add(
                    AdminUser(username=username, password_hash=hash_password(password))
                )
                await session.commit()
                logger.info("Bootstrap admin user '%s' created.", username)
        except Exception as exc:
            logger.warning("Bootstrap admin skipped (table may not exist yet): %s", exc)


async def _seed_sites_from_env() -> None:
    """
    If SITES env var is set and the sites table is empty, import them (credentials encrypted).
    Allows zero-downtime migration from env-only config to DB-managed sites.
    """
    env_sites = settings.sites
    if not env_sites:
        return

    from sqlalchemy import select, func
    from app.database import AsyncSessionLocal
    from app.models import Site
    from app.admin.crypto import encrypt

    async with AsyncSessionLocal() as session:
        try:
            count = (
                await session.execute(select(func.count()).select_from(Site))
            ).scalar_one()
            if count == 0:
                for s in env_sites:
                    session.add(
                        Site(
                            site_id=s.site_id,
                            name=s.site_id,
                            base_url=s.base_url,
                            wc_key_encrypted=encrypt(s.wc_key),
                            wc_secret_encrypted=encrypt(s.wc_secret),
                            is_active=True,
                        )
                    )
                await session.commit()
                logger.info("Seeded %d sites from SITES env var.", len(env_sites))
        except Exception as exc:
            logger.warning("Site seeding skipped (table may not exist yet): %s", exc)
