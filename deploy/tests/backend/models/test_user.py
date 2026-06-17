"""Tests for backend.models.user — User / auth / profile-update schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.models.user import (
    LoginRequest,
    PasswordChangeRequest,
    ProfileUpdateRequest,
    RefreshRequest,
    TokenResponse,
    User,
    UserCreate,
    UserResponse,
    UserUpdate,
)

# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


def test_user_defaults() -> None:
    u = User(id="u1", email="[email protected]", display_name="A", password_hash="x")
    assert u.role == "viewer"
    assert u.tenant_id == "_default"
    assert u.is_active is True
    assert u.last_login_at is None


def test_user_invalid_role_rejected() -> None:
    with pytest.raises(ValidationError):
        User(id="u1", email="[email protected]", display_name="A", password_hash="x", role="superuser")  # type: ignore[arg-type]


@pytest.mark.parametrize("role", ["admin", "editor", "viewer"])
def test_user_valid_roles(role: str) -> None:
    u = User(id="u1", email="[email protected]", display_name="A", password_hash="x", role=role)  # type: ignore[arg-type]
    assert u.role == role


# ---------------------------------------------------------------------------
# UserCreate
# ---------------------------------------------------------------------------


def test_user_create_minimal() -> None:
    uc = UserCreate(email="[email protected]", display_name="A", password="longpw1!")
    assert uc.role == "viewer"
    assert uc.tenant_id == "_default"


def test_user_create_short_password_rejected() -> None:
    with pytest.raises(ValidationError):
        UserCreate(email="[email protected]", display_name="A", password="short")


# ---------------------------------------------------------------------------
# ProfileUpdateRequest
# ---------------------------------------------------------------------------


def test_profile_update_request() -> None:
    r = ProfileUpdateRequest(display_name="New Name")
    assert r.display_name == "New Name"


def test_profile_update_request_empty_rejected() -> None:
    with pytest.raises(ValidationError):
        ProfileUpdateRequest(display_name="")


def test_profile_update_request_too_long_rejected() -> None:
    with pytest.raises(ValidationError):
        ProfileUpdateRequest(display_name="x" * 201)


# ---------------------------------------------------------------------------
# UserUpdate
# ---------------------------------------------------------------------------


def test_user_update_partial() -> None:
    u = UserUpdate(display_name="X")
    assert u.display_name == "X"
    assert u.role is None
    assert u.is_active is None


def test_user_update_admin_can_disable() -> None:
    u = UserUpdate(is_active=False)
    assert u.is_active is False


def test_user_update_invalid_role_rejected() -> None:
    with pytest.raises(ValidationError):
        UserUpdate(role="god")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# UserResponse
# ---------------------------------------------------------------------------


def test_user_response_no_password_hash() -> None:
    r = UserResponse(
        id="u1",
        email="[email protected]",
        display_name="A",
        role="admin",
        tenant_id="t1",
        is_active=True,
        created_at="2024-01-01T00:00:00",
        updated_at="2024-01-01T00:00:00",
    )
    assert "password_hash" not in r.model_dump()


# ---------------------------------------------------------------------------
# LoginRequest
# ---------------------------------------------------------------------------


def test_login_request() -> None:
    r = LoginRequest(email="[email protected]", password="hunter2")
    assert r.email == "[email protected]"


# ---------------------------------------------------------------------------
# TokenResponse
# ---------------------------------------------------------------------------


def test_token_response_defaults() -> None:
    r = TokenResponse(
        access_token="a",
        refresh_token="b",
        user=UserResponse(
            id="u1",
            email="[email protected]",
            display_name="A",
            role="admin",
            tenant_id="t1",
            is_active=True,
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        ),
    )
    assert r.token_type == "bearer"


# ---------------------------------------------------------------------------
# RefreshRequest
# ---------------------------------------------------------------------------


def test_refresh_request() -> None:
    r = RefreshRequest(refresh_token="x")
    assert r.refresh_token == "x"


# ---------------------------------------------------------------------------
# PasswordChangeRequest
# ---------------------------------------------------------------------------


def test_password_change_request() -> None:
    r = PasswordChangeRequest(current_password="old", new_password="newpw99!")
    assert r.new_password == "newpw99!"


def test_password_change_request_short_new_password_rejected() -> None:
    with pytest.raises(ValidationError):
        PasswordChangeRequest(current_password="old", new_password="x")
