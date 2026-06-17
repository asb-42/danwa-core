"""Tests for backend.core.security — JWT and password hashing.

This is the most security-critical module in the system, so the test
density here is intentionally high.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from jose import JWTError

from backend.core import security
from backend.core.config import Settings
from backend.core.security import (
    TokenData,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    user_to_response,
    verify_password,
)
from backend.models.user import User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force a known JWT secret for deterministic tests."""
    monkeypatch.setattr(security, "settings", Settings(jwt_secret_key="test-secret", jwt_algorithm="HS256"))


@pytest.fixture
def sample_user() -> User:
    return User(
        id="user-1",
        email="[email protected]",
        display_name="Alice",
        password_hash="x",
        role="admin",
        tenant_id="t1",
    )


# ---------------------------------------------------------------------------
# hash_password / verify_password
# ---------------------------------------------------------------------------


def test_hash_password_returns_hash_not_plaintext() -> None:
    h = hash_password("supersecret")
    assert h != "supersecret"
    assert h.startswith("$2")  # bcrypt prefix


def test_verify_password_round_trip() -> None:
    h = hash_password("hunter2")
    assert verify_password("hunter2", h) is True


def test_verify_password_wrong_password_returns_false() -> None:
    h = hash_password("hunter2")
    assert verify_password("WRONG", h) is False


def test_verify_password_garbled_hash_does_not_raise() -> None:
    """A malformed bcrypt hash must return False, not raise (P4.5+ §4.7)."""
    assert verify_password("hunter2", "not-a-real-bcrypt-hash") is False


def test_verify_password_empty_hash_returns_false() -> None:
    assert verify_password("hunter2", "") is False


# ---------------------------------------------------------------------------
# create_access_token
# ---------------------------------------------------------------------------


def test_create_access_token_contains_required_claims(jwt_secret: None, sample_user: User) -> None:
    token = create_access_token(sample_user)
    td = decode_token(token)
    assert td.user_id == sample_user.id
    assert td.email == sample_user.email
    assert td.role == "admin"
    assert td.tenant_id == "t1"
    assert td.token_type == "access"


def test_create_access_token_default_expiry(jwt_secret: None, sample_user: User) -> None:
    token = create_access_token(sample_user)
    # Decode via jose to inspect ``exp`` claim
    from jose import jwt as _jose

    payload = _jose.get_unverified_claims(token)
    assert "exp" in payload
    assert "iat" in payload
    assert "sub" in payload


def test_create_access_token_custom_expiry(jwt_secret: None, sample_user: User) -> None:
    token = create_access_token(sample_user, expires_delta=timedelta(minutes=5))
    td = decode_token(token)
    assert td.user_id == sample_user.id


def test_create_access_token_role_override(jwt_secret: None, sample_user: User) -> None:
    token = create_access_token(sample_user, role_override="viewer")
    td = decode_token(token)
    assert td.role == "viewer"


def test_create_access_token_tenant_override(jwt_secret: None, sample_user: User) -> None:
    token = create_access_token(sample_user, tenant_id="t2")
    td = decode_token(token)
    assert td.tenant_id == "t2"


# ---------------------------------------------------------------------------
# create_refresh_token
# ---------------------------------------------------------------------------


def test_create_refresh_token_token_type(jwt_secret: None, sample_user: User) -> None:
    token = create_refresh_token(sample_user)
    td = decode_token(token)
    assert td.token_type == "refresh"
    assert td.user_id == sample_user.id


def test_create_refresh_token_custom_expiry(jwt_secret: None, sample_user: User) -> None:
    token = create_refresh_token(sample_user, expires_delta=timedelta(days=1))
    td = decode_token(token)
    assert td.token_type == "refresh"


# ---------------------------------------------------------------------------
# decode_token — required-claims check
# ---------------------------------------------------------------------------


def test_decode_token_rejects_garbage(jwt_secret: None) -> None:
    with pytest.raises(JWTError):
        decode_token("not-a-jwt")


def test_decode_token_rejects_token_signed_with_wrong_key(jwt_secret: None) -> None:
    from jose import jwt as _jose

    bad = _jose.encode({"sub": "u1", "type": "access"}, "wrong-secret", algorithm="HS256")
    with pytest.raises(JWTError):
        decode_token(bad)


def test_decode_token_rejects_missing_sub(jwt_secret: None) -> None:
    from jose import jwt as _jose

    now = datetime.now(UTC)
    payload = {"iat": now, "exp": now + timedelta(minutes=5), "type": "access"}
    token = _jose.encode(payload, "test-secret", algorithm="HS256")
    with pytest.raises(JWTError) as ei:
        decode_token(token)
    assert "sub" in str(ei.value).lower() or "sub" in str(ei.value)


def test_decode_token_rejects_missing_iat(jwt_secret: None) -> None:
    from jose import jwt as _jose

    now = datetime.now(UTC)
    payload = {"sub": "u1", "exp": now + timedelta(minutes=5), "type": "access"}
    token = _jose.encode(payload, "test-secret", algorithm="HS256")
    with pytest.raises(JWTError):
        decode_token(token)


def test_decode_token_rejects_missing_exp(jwt_secret: None) -> None:
    from jose import jwt as _jose

    now = datetime.now(UTC)
    payload = {"sub": "u1", "iat": now, "type": "access"}
    token = _jose.encode(payload, "test-secret", algorithm="HS256")
    with pytest.raises(JWTError):
        decode_token(token)


def test_decode_token_rejects_empty_sub(jwt_secret: None) -> None:
    from jose import jwt as _jose

    now = datetime.now(UTC)
    payload = {"sub": "", "iat": now, "exp": now + timedelta(minutes=5), "type": "access"}
    token = _jose.encode(payload, "test-secret", algorithm="HS256")
    with pytest.raises(JWTError):
        decode_token(token)


def test_decode_token_rejects_expired(jwt_secret: None) -> None:
    from jose import jwt as _jose

    past = datetime.now(UTC) - timedelta(hours=2)
    payload = {"sub": "u1", "iat": past, "exp": past + timedelta(minutes=1), "type": "access"}
    token = _jose.encode(payload, "test-secret", algorithm="HS256")
    with pytest.raises(JWTError):
        decode_token(token)


# ---------------------------------------------------------------------------
# TokenData
# ---------------------------------------------------------------------------


def test_token_data_defaults() -> None:
    td = TokenData(user_id="u1")
    assert td.email == ""
    assert td.role == ""
    assert td.tenant_id == ""
    assert td.token_type == "access"


def test_token_data_with_all_fields() -> None:
    td = TokenData(user_id="u1", email="e", role="r", tenant_id="t", token_type="refresh")
    assert td.user_id == "u1"
    assert td.token_type == "refresh"


# ---------------------------------------------------------------------------
# user_to_response
# ---------------------------------------------------------------------------


def test_user_to_response_excludes_password_hash(sample_user: User) -> None:
    resp = user_to_response(sample_user)
    assert resp.id == sample_user.id
    assert resp.email == sample_user.email
    assert resp.role == "admin"
    assert resp.tenant_id == "t1"
    # ``UserResponse`` doesn't carry a password_hash field
    assert "password_hash" not in resp.model_dump()


def test_user_to_response_preserves_last_login(sample_user: User) -> None:
    ts = datetime(2024, 1, 1, 12, 0, 0)
    sample_user.last_login_at = ts
    resp = user_to_response(sample_user)
    assert resp.last_login_at == ts


# ---------------------------------------------------------------------------
# pwd_context is a real bcrypt context
# ---------------------------------------------------------------------------


def test_pwd_context_uses_bcrypt() -> None:
    assert "bcrypt" in security.pwd_context.schemes()
