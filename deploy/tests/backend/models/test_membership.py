"""Tests for backend.models.membership — TenantMembership + response."""

from __future__ import annotations

import pytest

from backend.models.membership import TenantMembership, TenantMembershipResponse


def test_membership_defaults() -> None:
    m = TenantMembership(tenant_id="t1", user_id="u1")
    assert m.role == "member"
    assert m.invited_by is None


def test_membership_admin_role() -> None:
    m = TenantMembership(tenant_id="t1", user_id="u1", role="admin")
    assert m.role == "admin"


def test_membership_response_includes_tenant_name() -> None:
    m = TenantMembership(tenant_id="t1", user_id="u1")
    r = TenantMembershipResponse(**m.model_dump(), tenant_name="My Tenant")
    assert r.tenant_name == "My Tenant"


def test_membership_response_optional_tenant_name() -> None:
    m = TenantMembership(tenant_id="t1", user_id="u1")
    r = TenantMembershipResponse(**m.model_dump())
    assert r.tenant_name is None
