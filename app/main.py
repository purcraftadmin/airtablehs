"""
WooCommerce Shared Inventory Sync Service – FastAPI entry point.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import admin, webhooks
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
    description="Multi-site WooCommerce SSOT inventory with propagation.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # restrict in production if needed
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhooks.router)
app.include_router(admin.router)


@app.on_event("startup")
async def _startup() -> None:
    logger.info("Starting propagation worker …")
    asyncio.create_task(propagation.worker(), name="propagation-worker")
    logger.info(
        "Inventory sync service ready. Sites: %s",
        [s.site_id for s in settings.sites],
    )


@app.on_event("shutdown")
async def _shutdown() -> None:
    logger.info("Draining propagation queue …")
    try:
        await asyncio.wait_for(propagation._queue.join(), timeout=30)
    except asyncio.TimeoutError:
        logger.warning("Propagation queue did not drain within 30 s")
