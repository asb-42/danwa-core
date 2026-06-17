"""Tests for backend.models.case — Case / CaseCreate / CaseUpdate / CaseResponse."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.models.case import (
    Case,
    CaseCreateRequest,
    CaseListItem,
    CaseResponse,
    CaseUpdateRequest,
)


def test_case_defaults() -> None:
    c = Case(title="My Case")
    assert c.tenant_id == "_default"
    assert c.status == "active"
    assert c.tags == []
    assert c.created_by == ""
    assert c.metadata == {}


def test_case_empty_title_rejected() -> None:
    with pytest.raises(ValidationError):
        Case(title="")


def test_case_long_title_rejected() -> None:
    with pytest.raises(ValidationError):
        Case(title="x" * 201)


def test_case_unique_ids() -> None:
    a = Case(title="A")
    b = Case(title="B")
    assert a.id != b.id


def test_case_create_request() -> None:
    r = CaseCreateRequest(title="T", description="D", tags=["a", "b"], created_by="u1")
    assert r.title == "T"
    assert r.tags == ["a", "b"]


def test_case_update_request_partial() -> None:
    r = CaseUpdateRequest(title="new")
    assert r.title == "new"
    assert r.description is None
    assert r.tags is None
    assert r.status is None


def test_case_response_dump() -> None:
    c = Case(title="T", description="D")
    r = CaseResponse(**c.model_dump())
    assert r.title == "T"
    assert r.id == c.id


def test_case_list_item_dump() -> None:
    c = Case(title="T")
    li = CaseListItem(**c.model_dump())
    assert li.title == "T"
