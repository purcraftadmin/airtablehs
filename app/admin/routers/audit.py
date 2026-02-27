"""
Admin audit log: inventory events + propagation failures.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import pop_flash
from app.admin.deps import AdminUser, require_admin
from app.admin.templates_cfg import templates
from app.database import get_db
from app.models import InventoryEvent, PropagationFailure

router = APIRouter()

_PAGE_SIZE = 50


@router.get("/admin/audit", response_class=HTMLResponse)
async def audit_log(
    request: Request,
    page: int = 1,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_admin),
) -> HTMLResponse:
    offset = max(0, (page - 1)) * _PAGE_SIZE

    events = (
        await db.execute(
            select(InventoryEvent)
            .order_by(InventoryEvent.created_at.desc())
            .offset(offset)
            .limit(_PAGE_SIZE)
        )
    ).scalars().all()

    failures = (
        await db.execute(
            select(PropagationFailure)
            .order_by(PropagationFailure.last_tried.desc())
            .limit(25)
        )
    ).scalars().all()

    return templates.TemplateResponse(
        "audit.html",
        {
            "request": request,
            "flash": pop_flash(request),
            "active": "audit",
            "user": current_user,
            "events": events,
            "failures": failures,
            "page": page,
            "page_size": _PAGE_SIZE,
        },
    )
