"""Tests for backend.core.seed — default tenant + admin user seeding."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.core import seed
from backend.core.seed import ensure_admin_user, ensure_default_tenant


def test_ensure_default_tenant_skips_when_tenants_exist() -> None:
    fake_store = MagicMock()
    fake_store.count.return_value = 3
    with patch.object(seed, "TenantStore", return_value=fake_store):
        ensure_default_tenant()
    fake_store.create.assert_not_called()


def test_ensure_default_tenant_creates_when_empty() -> None:
    fake_store = MagicMock()
    fake_store.count.return_value = 0
    with patch.object(seed, "TenantStore", return_value=fake_store):
        ensure_default_tenant()
    fake_store.create.assert_called_once_with(name="Default", plan="free", tenant_id="_default")


def test_ensure_admin_user_skips_when_users_exist() -> None:
    fake_store = MagicMock()
    fake_store.count.return_value = 5
    with patch.object(seed, "UserStore", return_value=fake_store):
        ensure_admin_user()
    fake_store.create.assert_not_called()


def test_ensure_admin_user_creates_when_empty() -> None:
    fake_user_store = MagicMock()
    fake_user_store.count.return_value = 0
    fake_tenant_store = MagicMock()
    fake_tenant_store.count.return_value = 0
    with patch.object(seed, "TenantStore", return_value=fake_tenant_store), \
         patch.object(seed, "UserStore", return_value=fake_user_store):
        ensure_admin_user()
    fake_user_store.create.assert_called_once()
    kwargs = fake_user_store.create.call_args.kwargs
    assert kwargs["email"] == "admin@danwa.local"
    assert kwargs["role"] == "admin"
    assert kwargs["tenant_id"] == "_default"
    # password_hash should be the bcrypt hash, NOT plaintext
    assert kwargs["password_hash"] != "changeme"
    assert kwargs["password_hash"].startswith("$2")


def test_ensure_admin_user_calls_ensure_default_tenant_first() -> None:
    """Admin creation must ensure the default tenant exists first."""
    fake_user_store = MagicMock()
    fake_user_store.count.return_value = 0
    fake_tenant_store = MagicMock()
    fake_tenant_store.count.return_value = 0
    with patch.object(seed, "TenantStore", return_value=fake_tenant_store), \
         patch.object(seed, "UserStore", return_value=fake_user_store):
        ensure_admin_user()
    fake_tenant_store.create.assert_called_once()
    fake_user_store.create.assert_called_once()
