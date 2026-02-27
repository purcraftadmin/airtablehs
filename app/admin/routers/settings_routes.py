"""
Admin settings: behaviour + Airtable config.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import pop_flash, set_flash
from app.admin.crypto import encrypt
from app.admin.deps import AdminUser, require_admin
from app.admin.templates_cfg import templates
from app.database import get_db
from app.models import AppSettings

router = APIRouter()


async def _get_or_create_settings(db: AsyncSession) -> AppSettings:
    row = await db.get(AppSettings, 1)
    if row is None:
        row = AppSettings(id=1)
        db.add(row)
        await db.flush()
    return row


@router.get("/admin/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_admin),
) -> HTMLResponse:
    cfg = await _get_or_create_settings(db)

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "flash": pop_flash(request),
            "active": "settings",
            "user": current_user,
            "cfg": cfg,
        },
    )


@router.post("/admin/settings")
async def settings_save(
    request: Request,
    decrement_status: str = Form("processing"),
    backorders_default: bool = Form(default=False),
    webhook_auth_mode: str = Form("hmac"),
    airtable_enabled: bool = Form(default=False),
    airtable_base_id: str = Form(""),
    airtable_table_names: str = Form(""),
    airtable_api_key: str = Form(""),
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_admin),
) -> RedirectResponse:
    cfg = await _get_or_create_settings(db)

    cfg.decrement_status = decrement_status.strip() or "processing"
    cfg.backorders_default = backorders_default
    cfg.webhook_auth_mode = webhook_auth_mode if webhook_auth_mode in ("hmac", "bearer") else "hmac"
    cfg.airtable_enabled = airtable_enabled
    cfg.airtable_base_id = airtable_base_id.strip() or None
    cfg.airtable_table_names = airtable_table_names.strip() or None

    # Only overwrite encrypted key if a new value was submitted
    if airtable_api_key.strip():
        cfg.airtable_api_key_encrypted = encrypt(airtable_api_key.strip())

    db.add(cfg)
    await db.commit()

    set_flash(request, "Settings saved.", "success")
    return RedirectResponse(url="/admin/settings", status_code=status.HTTP_303_SEE_OTHER)
