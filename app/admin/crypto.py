"""
Fernet symmetric encryption for WC credentials and Airtable API keys.
Never logs plaintext values.
"""
from __future__ import annotations

import base64
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        raw = os.environ.get("CONFIG_ENCRYPTION_KEY", "").strip()
        if not raw:
            raise RuntimeError(
                "CONFIG_ENCRYPTION_KEY is not set. "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        # Accept raw 32-byte keys (auto-encode to URL-safe base64) or Fernet keys
        try:
            _fernet = Fernet(raw.encode() if not raw.endswith("=") else raw.encode())
        except Exception:
            # Try treating raw bytes as a 32-char secret and derive Fernet key
            padded = base64.urlsafe_b64encode(raw[:32].ljust(32).encode())
            _fernet = Fernet(padded)
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string; returns a URL-safe base64 token string."""
    if not plaintext:
        return ""
    token = _get_fernet().encrypt(plaintext.encode())
    return token.decode()


def decrypt(token: str) -> str:
    """Decrypt a Fernet token back to plaintext. Raises InvalidToken if tampered."""
    if not token:
        return ""
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        logger.error("Failed to decrypt token – possible key mismatch or tampered data")
        raise
