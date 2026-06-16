"""Tests for the catalog normalizer.

We feed in tiny synthetic JSON samples that mirror the real
catwalk + llm_db shapes (verified against
https://github.com/charmbracelet/catwalk/blob/main/internal/providers/configs/openai.json
and
https://github.com/agentjido/llm_db/blob/main/priv/llm_db/providers/openai.json
in the planning phase) and assert the resulting NormalizedModel
fields are mapped correctly.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from backend.llm_catalog.normalize import (
    normalize_catwalk,
    normalize_llm_db,
    load_source_normalized,
)


CATWALK_OPENAI = {
    "name": "OpenAI",
    "id": "openai",
    "type": "openai",
    "api_key": "$OPENAI_API_KEY",
    "api_endpoint": "https://api.openai.com/v1",
    "default_large_model_id": "gpt-4o",
    "default_small_model_id": "gpt-4o-mini",
    "models": [
        {
            "id": "gpt-4o",
            "name": "GPT-4o",
            "cost_per_1m_in": 2.5,
            "cost_per_1m_out": 10.0,
            "cost_per_1m_in_cached": 1.25,
            "cost_per_1m_out_cached": 0.0,
            "context_window": 128000,
            "default_max_tokens": 16384,
            "can_reason": False,
            "reasoning_levels": [],
            "default_reasoning_effort": None,
            "supports_attachments": True,
        },
        {
            "id": "o1",
            "name": "o1",
            "cost_per_1m_in": 15.0,
            "cost_per_1m_out": 60.0,
            "context_window": 200000,
            "default_max_tokens": 100000,
            "can_reason": True,
            "reasoning_levels": ["low", "medium", "high"],
            "default_reasoning_effort": "medium",
            "supports_attachments": False,
        },
    ],
}


LLM_DB_OPENAI = {
    "id": "openai",
    "name": "OpenAI",
    "base_url": "https://api.openai.com/v1",
    "env": ["OPENAI_API_KEY"],
    "exclude_models": [],
    "models": {
        "gpt-4o": {
            "id": "gpt-4o",
            "name": "GPT-4o",
            "provider": "openai",
            "family": "gpt-4o",
            "aliases": ["gpt-4o-2024-08-06"],
            "capabilities": {
                "chat": True,
                "embeddings": False,
                "json": {"native": True, "schema": True, "strict": True},
                "reasoning": {"enabled": False},
                "streaming": {"tool_calls": True},
                "tools": {"enabled": True},
            },
            "cost": {"input": 0.0025, "output": 0.01, "cache_read": 0.00125, "cache_write": 0.0},
            "modalities": {"input": ["text", "image"], "output": ["text"]},
            "limits": {"context": 128000, "output": 16384},
            "lifecycle": {"status": "active"},
            "knowledge": "2024-06-30",
            "release_date": "2024-05-13",
            "last_updated": "2024-09-12",
            "deprecated": False,
            "retired": False,
        },
    },
}


def test_normalize_catwalk_maps_all_fields(tmp_path):
    p = tmp_path / "openai.json"
    p.write_text(json.dumps(CATWALK_OPENAI), encoding="utf-8")
    out = list(
        normalize_catwalk(p, "openai", "OpenAI", CATWALK_OPENAI)
    )
    assert len(out) == 2
    gpt = next(m for m in out if m.catalog_id == "gpt-4o")
    assert gpt.source == "catwalk"
    assert gpt.provider == "openai"
    assert gpt.provider_name == "OpenAI"
    assert gpt.name == "GPT-4o"
    assert gpt.cost_per_1m_input == 2.5
    assert gpt.cost_per_1m_output == 10.0
    assert gpt.cost_per_1m_cached_input == 1.25
    assert gpt.context_window == 128000
    assert gpt.max_tokens == 16384
    assert gpt.can_reason is False
    assert gpt.api_key_env == "OPENAI_API_KEY"  # $-prefix stripped
    assert gpt.api_endpoint_template == "https://api.openai.com/v1"
    # o1 has reasoning
    o1 = next(m for m in out if m.catalog_id == "o1")
    assert o1.can_reason is True
    assert o1.reasoning_levels == ["low", "medium", "high"]
    assert o1.default_reasoning_effort == "medium"


def test_normalize_catwalk_skips_malformed_entries(tmp_path):
    raw = {
        "id": "openai",
        "name": "OpenAI",
        "api_key": "$OPENAI_API_KEY",
        "models": [
            {"id": "good", "name": "Good", "context_window": 1000},
            {"name": "no-id"},  # missing id — should be skipped
            "string-not-dict",  # not a dict — should be skipped
            {"id": "", "name": "empty-id"},  # empty id — should be skipped
        ],
    }
    p = tmp_path / "openai.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    out = list(normalize_catwalk(p, "openai", "OpenAI", raw))
    assert len(out) == 1
    assert out[0].catalog_id == "good"


def test_normalize_llm_db_multplies_cost_by_1000(tmp_path):
    """llm_db stores cost per-1K; catwalk stores per-1M. The
    normalized shape is per-1M (matches catwalk's convention) so
    we multiply llm_db's per-1K values by 1000.
    """
    p = tmp_path / "openai.json"
    p.write_text(json.dumps(LLM_DB_OPENAI), encoding="utf-8")
    out = list(normalize_llm_db(p, "openai", "OpenAI", LLM_DB_OPENAI))
    assert len(out) == 1
    gpt = out[0]
    assert gpt.source == "llm_db"
    # 0.0025 * 1000 = 2.5 per-1M
    assert gpt.cost_per_1m_input == 2.5
    # 0.01 * 1000 = 10.0 per-1M
    assert gpt.cost_per_1m_output == 10.0
    # 0.00125 * 1000 = 1.25 per-1M
    assert gpt.cost_per_1m_cached_input == 1.25
    # capabilities + modalities come through directly
    assert gpt.capabilities == LLM_DB_OPENAI["models"]["gpt-4o"]["capabilities"]
    assert gpt.modalities == LLM_DB_OPENAI["models"]["gpt-4o"]["modalities"]
    assert gpt.lifecycle_status == "active"
    assert gpt.knowledge_cutoff == "2024-06-30"
    assert gpt.aliases == ["gpt-4o-2024-08-06"]


def test_load_source_normalized_swallows_per_file_errors(tmp_path):
    base = tmp_path / "openai.json"
    base.write_text(json.dumps(CATWALK_OPENAI), encoding="utf-8")
    bad = tmp_path / "broken.json"
    bad.write_text("{ not valid json", encoding="utf-8")
    out = load_source_normalized(_dummy_source("catwalk", str(tmp_path)), tmp_path)
    # Only the good file contributes
    assert len(out) == 2
    assert {m.catalog_id for m in out} == {"gpt-4o", "o1"}


def _dummy_source(name: str, path: str):  # type: ignore[no-untyped-def]
    """Build a minimal SourceSpec-like object for the loader tests."""
    from backend.llm_catalog.sources import SourceSpec

    return SourceSpec(
        name=name,
        repo_url="https://example.com/repo.git",
        branch="main",
        path=path,
    )


def test_load_source_normalized_returns_empty_for_missing_dir(tmp_path):
    out = load_source_normalized(_dummy_source("catwalk", "/nonexistent"), tmp_path)
    assert out == []
