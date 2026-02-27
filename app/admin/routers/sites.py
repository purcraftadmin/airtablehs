"""
Admin sites CRUD: list, create, edit, deactivate, refresh mapping.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import pop_flash, set_flash
from app.admin.crypto import decrypt, encrypt
from app.admin.deps import AdminUser, require_admin
from app.admin.templates_cfg import templates
from app.database import get_db
from app.models import Site, SiteSkuMap
from app.services.mapping import refresh_site_mappings
from app.config import SiteConfig

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/admin/sites", response_class=HTMLResponse)
async def sites_list(
    request: Request,
    q: str = "",
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_admin),
) -> HTMLResponse:
    stmt = select(Site).order_by(Site.name, Site.site_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            Site.name.ilike(like) | Site.site_id.ilike(like) | Site.base_url.ilike(like)
        )
    sites = (await db.execute(stmt)).scalars().all()

    # SKU count per site
    sku_counts: dict[str, int] = {}
    if sites:
        rows = (
            await db.execute(
                select(SiteSkuMap.site_id, func.count().label("cnt"))
                .where(SiteSkuMap.site_id.in_([s.site_id for s in sites]))
                .group_by(SiteSkuMap.site_id)
            )
        ).all()
        sku_counts = {r.site_id: r.cnt for r in rows}

    return templates.TemplateResponse(
        "sites/list.html",
        {
            "request": request,
            "flash": pop_flash(request),
            "active": "sites",
            "user": current_user,
            "sites": sites,
            "sku_counts": sku_counts,
            "q": q,
        },
    )


@router.get("/admin/sites/new", response_class=HTMLResponse)
async def sites_new(
    request: Request,
    current_user: AdminUser = Depends(require_admin),
) -> HTMLResponse:
    return templates.TemplateResponse(
        "sites/form.html",
        {
            "request": request,
            "flash": pop_flash(request),
            "active": "sites",
            "user": current_user,
            "site": None,
            "errors": {},
        },
    )


@router.post("/admin/sites", response_class=HTMLResponse)
async def sites_create(
    request: Request,
    name: str = Form(...),
    site_id: str = Form(...),
    base_url: str = Form(...),
    wc_key: str = Form(...),
    wc_secret: str = Form(...),
    is_active: bool = Form(default=True),
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_admin),
) -> RedirectResponse | HTMLResponse:
    errors: dict[str, str] = {}

    site_id = site_id.strip().lower().replace(" ", "-")
    base_url = base_url.strip().rstrip("/")

    if not site_id:
        errors["site_id"] = "Site ID is required."
    if not base_url.startswith("http"):
        errors["base_url"] = "Base URL must start with http:// or https://"
    if not wc_key.strip():
        errors["wc_key"] = "Consumer key is required."
    if not wc_secret.strip():
        errors["wc_secret"] = "Consumer secret is required."

    # Duplicate check
    if not errors.get("site_id"):
        existing = (
            await db.execute(select(Site).where(Site.site_id == site_id))
        ).scalar_one_or_none()
        if existing:
            errors["site_id"] = f"Site ID '{site_id}' already exists."

    if errors:
        return templates.TemplateResponse(
            "sites/form.html",
            {
                "request": request,
                "flash": None,
                "active": "sites",
                "user": current_user,
                "site": None,
                "form": {"name": name, "site_id": site_id, "base_url": base_url},
                "errors": errors,
            },
            status_code=422,
        )

    site = Site(
        name=name.strip(),
        site_id=site_id,
        base_url=base_url,
        wc_key_encrypted=encrypt(wc_key.strip()),
        wc_secret_encrypted=encrypt(wc_secret.strip()),
        is_active=is_active,
    )
    db.add(site)
    await db.commit()

    set_flash(request, f"Site '{site_id}' added successfully.", "success")
    return RedirectResponse(url="/admin/sites", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/admin/sites/{site_uuid}/edit", response_class=HTMLResponse)
async def sites_edit(
    site_uuid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_admin),
) -> HTMLResponse:
    site = await db.get(Site, site_uuid)
    if not site:
        set_flash(request, "Site not found.", "error")
        return RedirectResponse(url="/admin/sites", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(
        "sites/form.html",
        {
            "request": request,
            "flash": pop_flash(request),
            "active": "sites",
            "user": current_user,
            "site": site,
            "errors": {},
        },
    )


@router.post("/admin/sites/{site_uuid}")
async def sites_update(
    site_uuid: str,
    request: Request,
    name: str = Form(...),
    base_url: str = Form(...),
    wc_key: str = Form(""),
    wc_secret: str = Form(""),
    is_active: bool = Form(default=True),
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_admin),
) -> RedirectResponse | HTMLResponse:
    site = await db.get(Site, site_uuid)
    if not site:
        set_flash(request, "Site not found.", "error")
        return RedirectResponse(url="/admin/sites", status_code=status.HTTP_303_SEE_OTHER)

    errors: dict[str, str] = {}
    base_url = base_url.strip().rstrip("/")
    if not base_url.startswith("http"):
        errors["base_url"] = "Base URL must start with http:// or https://"

    if errors:
        return templates.TemplateResponse(
            "sites/form.html",
            {
                "request": request,
                "flash": None,
                "active": "sites",
                "user": current_user,
                "site": site,
                "errors": errors,
            },
            status_code=422,
        )

    site.name = name.strip()
    site.base_url = base_url
    site.is_active = is_active
    # Only update credentials if new values were provided
    if wc_key.strip():
        site.wc_key_encrypted = encrypt(wc_key.strip())
    if wc_secret.strip():
        site.wc_secret_encrypted = encrypt(wc_secret.strip())

    db.add(site)
    await db.commit()

    set_flash(request, f"Site '{site.site_id}' updated.", "success")
    return RedirectResponse(url="/admin/sites", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/admin/sites/{site_uuid}/deactivate")
async def sites_deactivate(
    site_uuid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_admin),
) -> RedirectResponse:
    site = await db.get(Site, site_uuid)
    if site:
        site.is_active = not site.is_active
        db.add(site)
        await db.commit()
        state = "activated" if site.is_active else "deactivated"
        set_flash(request, f"Site '{site.site_id}' {state}.", "success")
    else:
        set_flash(request, "Site not found.", "error")
    return RedirectResponse(url="/admin/sites", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/admin/sites/{site_uuid}/refresh-mapping")
async def sites_refresh_mapping(
    site_uuid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_admin),
) -> RedirectResponse:
    site_row = await db.get(Site, site_uuid)
    if not site_row:
        set_flash(request, "Site not found.", "error")
        return RedirectResponse(url="/admin/sites", status_code=status.HTTP_303_SEE_OTHER)

    try:
        site_cfg = SiteConfig(
            site_id=site_row.site_id,
            base_url=site_row.base_url,
            wc_key=decrypt(site_row.wc_key_encrypted),
            wc_secret=decrypt(site_row.wc_secret_encrypted),
        )
        result = await refresh_site_mappings(db, site_cfg)

        # Update last_sync_at
        site_row.last_sync_at = datetime.now(timezone.utc)
        db.add(site_row)
        await db.commit()

        msg = f"Mapping refreshed for '{site_row.site_id}': {result.inserted} SKUs mapped."
        if result.errors:
            msg += f" ({len(result.errors)} errors)"
        set_flash(request, msg, "success" if not result.errors else "error")
    except Exception as exc:
        logger.exception("Mapping refresh failed for %s", site_row.site_id)
        set_flash(request, f"Mapping refresh failed: {exc}", "error")

    return RedirectResponse(url="/admin/sites", status_code=status.HTTP_303_SEE_OTHER)
