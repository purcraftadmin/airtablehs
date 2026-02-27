"""
Admin auth routes: login / logout.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import (
    clear_admin_session,
    pop_flash,
    set_admin_session,
    set_flash,
    verify_password,
)
from app.admin.templates_cfg import templates
from app.database import get_db
from app.models import AdminUser

router = APIRouter()


@router.get("/admin/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    flash = pop_flash(request)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "flash": flash},
    )


@router.post("/admin/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    user = (
        await db.execute(
            select(AdminUser).where(AdminUser.username == username)
        )
    ).scalar_one_or_none()

    if not user or not user.is_active or not verify_password(password, user.password_hash):
        set_flash(request, "Invalid username or password.", "error")
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)

    set_admin_session(request, user.id)
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/admin/logout")
async def logout(request: Request) -> RedirectResponse:
    clear_admin_session(request)
    return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)
