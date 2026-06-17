"""Tests for backend.models.tenant."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.models.tenant import Tenant, TenantCreate, TenantResponse, TenantUpdate


def test_tenant_defaults() -> None:
    t = Tenant(name="Acme")
    assert t.plan == "free"
    assert t.max_projects == 5
    assert t.max_concurrent_debates == 2
    assert t.max_documents == 50
    assert t.max_storage_mb == 500
    assert t.is_active is True
    assert t.settings == {}


def test_tenant_empty_name_rejected() -> None:
    with pytest.raises(ValidationError):
        Tenant(name="")


def test_tenant_invalid_plan_rejected() -> None:
    with pytest.raises(ValidationError):
        Tenant(name="X", plan="starter")  # type: ignore[arg-type]


@pytest.mark.parametrize("plan", ["free", "pro", "enterprise"])
def test_tenant_valid_plans(plan: str) -> None:
    t = Tenant(name="X", plan=plan)  # type: ignore[arg-type]
    assert t.plan == plan


def test_tenant_create() -> None:
    tc = TenantCreate(name="X", plan="pro")
    assert tc.name == "X"
    assert tc.plan == "pro"


def test_tenant_update_partial() -> None:
    u = TenantUpdate(name="X")
    assert u.name == "X"
    assert u.max_projects is None


def test_tenant_response() -> None:
    t = Tenant(name="X")
    r = TenantResponse(**t.model_dump())
    assert r.name == "X"
