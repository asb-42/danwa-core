"""End-to-end test of the import engine: build_diff + apply_diff.

The apply step writes into ``backend.modules.service.MODULES_DIR``,
so we monkeypatch that to a tempdir before each test.  This way no
real on-disk modules are touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import backend.modules.service as _svc
from backend.llm_catalog.id_strategy import module_id_for_provider_model
from backend.llm_catalog.import_engine import (
    apply_diff,
    build_diff,
    _merge_models,
)
from backend.llm_catalog.normalize import NormalizedModel


def _make_model(*, source: str, provider: str, catalog_id: str, name: str = "GPT-4o",
                cost_in: float = 2.5, cost_out: float = 10.0, context: int = 128000,
                capabilities: dict | None = None, modalities: dict | None = None) -> NormalizedModel:
    return NormalizedModel(
        source=source, provider=provider, provider_name=provider.capitalize(),
        catalog_id=catalog_id, name=name,
        api_base="https://api.openai.com/v1", api_key_env="OPENAI_API_KEY",
        context_window=context, max_tokens=16384,
        cost_per_1m_input=cost_in, cost_per_1m_output=cost_out,
        cost_per_1m_cached_input=None, cost_per_1m_cached_output=None,
        can_reason=False, reasoning_levels=[], default_reasoning_effort=None,
        capabilities=capabilities or {},
        modalities=modalities or {},
        lifecycle_status=None, knowledge_cutoff=None, release_date=None,
        last_updated=None, family=None, aliases=[], tags=[],
        api_endpoint_template=None, default_large_model_id=None,
        default_small_model_id=None, raw={},
    )


@pytest.fixture
def temp_modules_dir(tmp_path, monkeypatch):
    """Point MODULES_DIR at a tempdir so apply_diff doesn't touch real files."""
    modules = tmp_path / "modules"
    modules.mkdir()
    (modules / "llm-profiles").mkdir()
    monkeypatch.setattr(_svc, "MODULES_DIR", str(modules))
    return modules


def test_build_diff_against_empty_local_reports_create(temp_modules_dir):
    m = _make_model(source="catwalk", provider="openai", catalog_id="gpt-4o")
    diff = build_diff([m], None)
    # build_diff only populates actions that actually occur; apply_diff
    # seeds all 4 keys with zeros. Test against the more specific count.
    assert diff.by_action.get("create", 0) == 1
    assert diff.by_action.get("update", 0) == 0
    assert diff.by_action.get("stale", 0) == 0
    assert diff.by_action.get("skip", 0) == 0
    [entry] = diff.entries
    assert entry.action == "create"
    assert entry.module_id == module_id_for_provider_model("openai", "gpt-4o")


def test_apply_writes_manifest_and_profile_yaml(temp_modules_dir):
    m = _make_model(source="catwalk", provider="openai", catalog_id="gpt-4o-mini",
                    name="GPT-4o mini", cost_in=0.15, cost_out=0.6)
    diff = build_diff([m], None)
    report = apply_diff(diff, [m], None)
    assert report.by_action == {"create": 1, "update": 0, "stale": 0, "skip": 0}
    mid = module_id_for_provider_model("openai", "gpt-4o-mini")
    target = temp_modules_dir / "llm-profiles" / mid
    assert (target / "manifest.json").exists()
    assert (target / "profile.yaml").exists()

    import json, yaml
    manifest = json.loads((target / "manifest.json").read_text())
    profile = yaml.safe_load((target / "profile.yaml").read_text())
    assert manifest["type"] == "llm-profile"
    assert manifest["catalog_source"] == "catwalk"
    assert manifest["catalog_id"] == "gpt-4o-mini"
    assert profile["provider"] == "openai"
    assert profile["model"] == "gpt-4o-mini"
    # per-1K derived from per-1M (2.5 / 1000 etc.)
    assert profile["cost_per_1k_input"] == 0.00015
    assert profile["cost_per_1k_output"] == 0.0006


def test_apply_second_time_with_unchanged_model_reports_skip(temp_modules_dir):
    m = _make_model(source="catwalk", provider="openai", catalog_id="gpt-4o")
    apply_diff(build_diff([m], None), [m], None)
    diff2 = build_diff([m], None)
    # build_diff only populates actions that occur; use .get() with default
    assert diff2.by_action.get("skip", 0) == 1
    assert diff2.by_action.get("create", 0) == 0
    assert diff2.by_action.get("update", 0) == 0
    assert diff2.by_action.get("stale", 0) == 0


def test_apply_with_upstream_change_reports_update(temp_modules_dir):
    m = _make_model(source="catwalk", provider="openai", catalog_id="gpt-4o",
                    cost_out=10.0)
    apply_diff(build_diff([m], None), [m], None)
    m2 = _make_model(source="catwalk", provider="openai", catalog_id="gpt-4o",
                     cost_out=12.0)
    diff2 = build_diff([m2], None)
    assert diff2.by_action.get("update", 0) == 1
    assert diff2.by_action.get("create", 0) == 0
    assert diff2.by_action.get("stale", 0) == 0
    assert diff2.by_action.get("skip", 0) == 0
    [entry] = diff2.entries
    assert entry.action == "update"
    # _diff_entry now projects per-1M to per-1K before comparing
    assert any("cost_per_1k_output" in c for c in entry.changes)


def test_local_dir_not_in_catalog_marked_stale(temp_modules_dir):
    # Plant a local module that the catalog doesn't know about
    stale_dir = temp_modules_dir / "llm-profiles" / "llm-aaaaaaaaaaaaaaaa"
    stale_dir.mkdir()
    (stale_dir / "profile.yaml").write_text("id: aaaaaaaaaaaaaaaa\n")
    (stale_dir / "manifest.json").write_text("{}")

    diff = build_diff([], None)
    assert diff.by_action.get("stale", 0) == 1
    [entry] = diff.entries
    assert entry.action == "stale"
    assert entry.module_id == "llm-aaaaaaaaaaaaaaaa"


def test_merge_models_picks_catwalk_for_cost_and_llmdb_for_modalities():
    catwalk = _make_model(source="catwalk", provider="openai", catalog_id="gpt-4o",
                          cost_in=2.5, cost_out=10.0)
    lldb = _make_model(
        source="llm_db", provider="openai", catalog_id="gpt-4o",
        cost_in=2.5, cost_out=10.0,  # already per-1M
        capabilities={"chat": True, "tools": {"enabled": True}},
        modalities={"input": ["text", "image"], "output": ["text"]},
    )
    merged = _merge_models([catwalk, lldb])
    assert merged is not None
    assert merged["source"] == "catwalk"          # catwalk wins
    assert merged["modalities"] == {"input": ["text", "image"], "output": ["text"]}
    assert merged["capabilities"] == {"chat": True, "tools": {"enabled": True}}
    assert set(merged["merged_from"]) == {"catwalk", "llm_db"}
