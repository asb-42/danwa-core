"""Tests for backend.models.tag."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.models.tag import Tag, TagCreateRequest, TagResponse, TagUpdateRequest


def test_tag_defaults() -> None:
    t = Tag(name="My Tag")
    assert t.tenant_id == "_default"
    assert t.color == "#6366f1"
    assert t.parent_id is None
    assert t.id


def test_tag_empty_name_rejected() -> None:
    with pytest.raises(ValidationError):
        Tag(name="")


def test_tag_long_name_rejected() -> None:
    with pytest.raises(ValidationError):
        Tag(name="x" * 101)


def test_tag_create_request() -> None:
    r = TagCreateRequest(name="n", color="#fff", parent_id="p1")
    assert r.parent_id == "p1"


def test_tag_update_request_partial() -> None:
    r = TagUpdateRequest(name="n")
    assert r.name == "n"
    assert r.color is None


def test_tag_response() -> None:
    t = Tag(name="n")
    r = TagResponse(**t.model_dump())
    assert r.name == "n"
    assert r.tenant_id == "_default"
