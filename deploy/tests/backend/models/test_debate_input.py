"""Tests for backend.models.debate_input — DebateInput + InputAttachment."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.models.debate_input import DebateInput, InputAttachment


def test_attachment_defaults() -> None:
    a = InputAttachment(content_ref="file://x.txt")
    assert a.mime_type == "text/plain"
    assert a.description == ""
    assert a.extracted_text is None
    assert a.id  # auto-generated


def test_attachment_unique_ids() -> None:
    a = InputAttachment(content_ref="x")
    b = InputAttachment(content_ref="y")
    assert a.id != b.id


def test_debate_input_required_topic() -> None:
    with pytest.raises(ValidationError):
        DebateInput(source_plugin_key="standard_text")  # type: ignore[call-arg]


def test_debate_input_minimal() -> None:
    di = DebateInput(source_plugin_key="standard_text", topic="hello")
    assert di.topic == "hello"
    assert di.attachments == []
    assert di.session_id is None
    assert di.context_overrides == {}


def test_debate_input_auto_hash() -> None:
    di = DebateInput(source_plugin_key="standard_text", topic="hello")
    # input_hash must be auto-computed
    assert di.input_hash
    assert len(di.input_hash) == 64  # SHA-256 hex


def test_debate_input_hash_deterministic() -> None:
    a = DebateInput(source_plugin_key="standard_text", topic="same")
    b = DebateInput(source_plugin_key="standard_text", topic="same")
    assert a.input_hash == b.input_hash


def test_debate_input_hash_differs_for_different_topic() -> None:
    a = DebateInput(source_plugin_key="standard_text", topic="A")
    b = DebateInput(source_plugin_key="standard_text", topic="B")
    assert a.input_hash != b.input_hash


def test_debate_input_with_attachments() -> None:
    a = InputAttachment(content_ref="file://x.txt", mime_type="text/plain")
    di = DebateInput(source_plugin_key="standard_text", topic="t", attachments=[a])
    assert len(di.attachments) == 1


def test_debate_input_explicit_hash_preserved() -> None:
    """If input_hash is provided, post_init must not overwrite it."""
    di = DebateInput(
        source_plugin_key="standard_text", topic="t", input_hash="deadbeef" * 8,
    )
    assert di.input_hash == "deadbeef" * 8


def test_compute_hash_method() -> None:
    di = DebateInput(source_plugin_key="standard_text", topic="t")
    h = di.compute_hash()
    assert len(h) == 64
