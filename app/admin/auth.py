"""
Password hashing and session helpers.
"""
from __future__ import annotations

import logging

from fastapi import Request
from passlib.context import CryptContext

logger = logging.getLogger(__name__)

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


# ── Flash messages (stored in session) ──────────────────────────────────────

def set_flash(request: Request, message: str, kind: str = "success") -> None:
    request.session["flash"] = {"message": message, "kind": kind}


def pop_flash(request: Request) -> dict | None:
    return request.session.pop("flash", None)


# ── Session user helpers ─────────────────────────────────────────────────────

def set_admin_session(request: Request, user_id: str) -> None:
    request.session["admin_user_id"] = user_id


def clear_admin_session(request: Request) -> None:
    request.session.pop("admin_user_id", None)


def get_session_user_id(request: Request) -> str | None:
    return request.session.get("admin_user_id")
