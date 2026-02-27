"""
FastAPI dependency utilities: webhook signature verification, DB session.
"""
from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import Header, HTTPException, Request, status

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


async def verify_webhook(
    request: Request,
    x_wc_webhook_signature: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> bytes:
    """
    Verify webhook authenticity via HMAC-SHA256 or Bearer token.
    Returns the raw request body so routers don't need to re-read it.
    """
    body = await request.body()

    # ── Option 1: Bearer token ───────────────────────────────────────────────
    if settings.webhook_bearer_token:
        if authorization and authorization.startswith("Bearer "):
            token = authorization.removeprefix("Bearer ").strip()
            if hmac.compare_digest(token, settings.webhook_bearer_token):
                return body
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Bearer token",
        )

    # ── Option 2: HMAC SHA256 (WooCommerce default) ──────────────────────────
    if not settings.webhook_shared_secret:
        logger.warning("No WEBHOOK_SHARED_SECRET configured – accepting all webhooks!")
        return body

    if not x_wc_webhook_signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-WC-Webhook-Signature header",
        )

    expected = hmac.new(
        key=settings.webhook_shared_secret.encode(),
        msg=body,
        digestmod=hashlib.sha256,
    ).digest()

    import base64
    try:
        provided = base64.b64decode(x_wc_webhook_signature)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed signature header",
        )

    if not hmac.compare_digest(expected, provided):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook signature mismatch",
        )

    return body
