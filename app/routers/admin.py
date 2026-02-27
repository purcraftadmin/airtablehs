"""
Admin / operational endpoints.

GET  /admin/health
GET  /admin/stock/{sku}
GET  /admin/stock
POST /admin/refresh-mappings/{site_id}
POST /admin/refresh-mappings          (all sites)
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import Stock
from app.schemas import HealthResponse, MappingRefreshResult, StockRow
from app.services.mapping import refresh_site_mappings

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/health", response_model=HealthResponse)
async def health(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:
        logger.error("DB health check failed: %s", exc)
        db_status = "error"
    return HealthResponse(status="ok", db=db_status)


@router.get("/stock", response_model=List[StockRow])
async def list_stock(db: AsyncSession = Depends(get_db)) -> List[StockRow]:
    rows = (await db.execute(select(Stock).order_by(Stock.sku))).scalars().all()
    return [
        StockRow(
            sku=r.sku,
            on_hand=r.on_hand,
            reserved=r.reserved,
            updated_at=r.updated_at.isoformat(),
        )
        for r in rows
    ]


@router.get("/stock/{sku}", response_model=StockRow)
async def get_sku_stock(sku: str, db: AsyncSession = Depends(get_db)) -> StockRow:
    row = await db.get(Stock, sku)
    if row is None:
        raise HTTPException(status_code=404, detail=f"SKU {sku!r} not found")
    return StockRow(
        sku=row.sku,
        on_hand=row.on_hand,
        reserved=row.reserved,
        updated_at=row.updated_at.isoformat(),
    )


@router.post("/refresh-mappings", response_model=List[MappingRefreshResult])
async def refresh_all_mappings(
    db: AsyncSession = Depends(get_db),
) -> List[MappingRefreshResult]:
    results = []
    for site in settings.sites:
        result = await refresh_site_mappings(db, site)
        results.append(result)
    return results


@router.post("/refresh-mappings/{site_id}", response_model=MappingRefreshResult)
async def refresh_one_mapping(
    site_id: str,
    db: AsyncSession = Depends(get_db),
) -> MappingRefreshResult:
    site = settings.sites_by_id.get(site_id)
    if not site:
        raise HTTPException(status_code=404, detail=f"site_id {site_id!r} not configured")
    return await refresh_site_mappings(db, site)
