"""
Admin dependency: require authenticated admin session.
Raises AdminNotAuthenticated which the exception handler converts to a redirect.
"""
from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import get_session_user_id
from app.database import get_db
from app.models import AdminUser


class AdminNotAuthenticated(Exception):
    """Raised when admin session is missing or invalid – triggers redirect to login."""
    pass


async def require_admin(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    user_id = get_session_user_id(request)
    if not user_id:
        raise AdminNotAuthenticated()

    user = await db.get(AdminUser, user_id)
    if not user or not user.is_active:
        from app.admin.auth import clear_admin_session
        clear_admin_session(request)
        raise AdminNotAuthenticated()

    return user
