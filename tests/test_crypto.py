"""
Unit tests for Fernet encryption roundtrip.
"""
from __future__ import annotations

import os

import pytest
from cryptography.fernet import Fernet, InvalidToken


# Set a valid test key before importing crypto module
_TEST_KEY = Fernet.generate_key().decode()
os.environ["CONFIG_ENCRYPTION_KEY"] = _TEST_KEY


from app.admin.crypto import decrypt, encrypt, _fernet  # noqa: E402


@pytest.fixture(autouse=True)
def reset_fernet():
    """Reset cached Fernet instance so key changes take effect."""
    import app.admin.crypto as crypto_mod
    crypto_mod._fernet = None
    yield
    crypto_mod._fernet = None


def test_encrypt_decrypt_roundtrip():
    plaintext = "ck_abc123_consumer_key"
    token = encrypt(plaintext)
    assert decrypt(token) == plaintext


def test_encrypt_produces_different_tokens():
    """Fernet is non-deterministic – same plaintext produces different ciphertext each time."""
    p = "same_secret"
    t1 = encrypt(p)
    t2 = encrypt(p)
    assert t1 != t2
    # Both still decrypt to the same plaintext
    assert decrypt(t1) == p
    assert decrypt(t2) == p


def test_decrypt_invalid_token_raises():
    with pytest.raises(InvalidToken):
        decrypt("not-a-valid-fernet-token")


def test_encrypt_empty_string():
    assert encrypt("") == ""
    assert decrypt("") == ""


def test_roundtrip_special_characters():
    secret = "cs_l0ng!Secret$With#Special&Chars==//"
    assert decrypt(encrypt(secret)) == secret


def test_missing_key_raises_runtime_error():
    import app.admin.crypto as crypto_mod
    crypto_mod._fernet = None
    original = os.environ.pop("CONFIG_ENCRYPTION_KEY", None)
    try:
        with pytest.raises(RuntimeError, match="CONFIG_ENCRYPTION_KEY"):
            encrypt("test")
    finally:
        if original:
            os.environ["CONFIG_ENCRYPTION_KEY"] = original
        crypto_mod._fernet = None
