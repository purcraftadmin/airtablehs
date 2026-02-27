"""
Admin dashboard: summary stats + recent activity.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import pop_flash
from app.admin.deps import AdminUser, require_admin
from app.admin.templates_cfg import templates
from app.database import get_db
from app.models import InventoryEvent, PropagationFailure, Site, SiteSkuMap

router = APIRouter()


@router.get("/admin", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_admin),
) -> HTMLResponse:
    # ── Stats ────────────────────────────────────────────────────────────────
    active_sites = (
        await db.execute(
            select(func.count()).select_from(Site).where(Site.is_active == True)
        )
    ).scalar_one()

    total_skus = (
        await db.execute(
            select(func.count()).select_from(SiteSkuMap)
        )
    ).scalar_one()

    failure_count = (
        await db.execute(
            select(func.count()).select_from(PropagationFailure)
        )
    ).scalar_one()

    # ── Recent events (last 20) ───────────────────────────────────────────────
    recent_events = (
        await db.execute(
            select(InventoryEvent)
            .order_by(InventoryEvent.created_at.desc())
            .limit(20)
        )
    ).scalars().all()

    # ── Recent failures ───────────────────────────────────────────────────────
    recent_failures = (
        await db.execute(
            select(PropagationFailure)
            .order_by(PropagationFailure.last_tried.desc())
            .limit(5)
        )
    ).scalars().all()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "flash": pop_flash(request),
            "active": "dashboard",
            "user": current_user,
            "stats": {
                "active_sites": active_sites,
                "total_skus": total_skus,
                "failure_count": failure_count,
            },
            "recent_events": recent_events,
            "recent_failures": recent_failures,
        },
    )
