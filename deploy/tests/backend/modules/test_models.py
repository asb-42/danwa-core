"""Tests for backend.modules.models — module manifest Pydantic schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.modules.models import (
    AgentPersonaData,
    LanguagePackData,
    LLMProfileData,
    ManifestCompatibility,
    ManifestRepository,
    ModuleCategory,
    ModuleDependencies,
    ModuleFile,
    ModuleManifest,
    ModuleType,
    RoleTypeProfile,
    ToneProfileData,
    WorkflowTemplateData,
)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


def test_module_type_values() -> None:
    assert ModuleType.AGENT_PERSONA == "agent-persona"
    assert ModuleType.LLM_PROFILE == "llm-profile"
    assert ModuleType.WORKFLOW_TEMPLATE == "workflow-template"
    assert ModuleType.TONE_PROFILE == "tone-profile"
    assert ModuleType.BUNDLE == "bundle"
    assert ModuleType.AGENT_CORE == "agent-core"


def test_module_category_values() -> None:
    assert ModuleCategory.PROMPTS == "prompts"
    assert ModuleCategory.AGENTS == "agents"
    assert ModuleCategory.LLM_PROFILES == "llm-profiles"
    assert ModuleCategory.WORKFLOWS == "workflows"


# ---------------------------------------------------------------------------
# ModuleFile
# ---------------------------------------------------------------------------


def test_module_file_minimal() -> None:
    f = ModuleFile(path="x.md", format="markdown")
    assert f.checksum == ""
    assert f.role_type_id is None
    assert f.mode is None
    assert f.language is None


def test_module_file_invalid_format_warning() -> None:
    """``format`` is a free string but allowed values are markdown/yaml/json."""
    f = ModuleFile(path="x", format="text")
    assert f.format == "text"


# ---------------------------------------------------------------------------
# ModuleDependencies
# ---------------------------------------------------------------------------


def test_module_dependencies_defaults() -> None:
    d = ModuleDependencies()
    assert d.modules == {}
    assert d.roles == []


def test_module_dependencies_with_entries() -> None:
    d = ModuleDependencies(modules={"a": ">=1.0"}, roles=["strategist"])
    assert d.modules == {"a": ">=1.0"}
    assert d.roles == ["strategist"]


# ---------------------------------------------------------------------------
# ManifestCompatibility / ManifestRepository
# ---------------------------------------------------------------------------


def test_manifest_compatibility_defaults() -> None:
    c = ManifestCompatibility()
    assert c.danwa_min_version is None
    assert c.danwa_max_version is None


def test_manifest_repository_defaults() -> None:
    r = ManifestRepository()
    assert r.type == "github"
    assert r.url == ""


# ---------------------------------------------------------------------------
# ModuleManifest
# ---------------------------------------------------------------------------


def _minimal_manifest() -> dict:
    return {
        "schema_version": "2.0.0",
        "module_id": "my-mod",
        "name": {"en": "My Module"},
        "version": "1.0.0",
        "type": "agent-persona",
        "category": "agents",
        "profile_file": "profile.yaml",
        "profile_format": "yaml",
    }


def test_module_manifest_minimal() -> None:
    m = ModuleManifest(**_minimal_manifest())
    assert m.module_id == "my-mod"
    assert m.license == "CC-BY-4.0"
    assert m.dependencies == ModuleDependencies()


def test_module_manifest_invalid_id_rejected() -> None:
    data = _minimal_manifest()
    data["module_id"] = "INVALID"
    with pytest.raises(ValidationError):
        ModuleManifest(**data)


def test_module_manifest_unknown_type_rejected() -> None:
    data = _minimal_manifest()
    data["type"] = "not-a-real-type"
    with pytest.raises(ValidationError):
        ModuleManifest(**data)


def test_module_manifest_default_schema_version() -> None:
    data = _minimal_manifest()
    del data["schema_version"]
    m = ModuleManifest(**data)
    assert m.schema_version == "2.0.0"


# ---------------------------------------------------------------------------
# Profile data classes
# ---------------------------------------------------------------------------


def test_role_type_profile_minimal() -> None:
    r = RoleTypeProfile(id="r1", name="Strategist")
    assert r.category == "functional"
    assert r.default_max_rounds == 5
    assert r.default_consensus_threshold == 0.9
    assert r.is_active is True


def test_tone_profile_data_minimal() -> None:
    t = ToneProfileData(id="t1", name="Formal")
    assert t.style == "neutral"
    assert t.formality == 0.5
    assert t.verbosity == "medium"


def test_llm_profile_data_minimal() -> None:
    p = LLMProfileData(id="p1", name="X", provider="openai", model="gpt-4o")
    assert p.max_tokens == 4096
    assert p.temperature == 0.7
    assert p.protocol == "litellm"
    assert p.service_eligible is True


def test_agent_persona_data_minimal() -> None:
    a = AgentPersonaData(
        id="p1",
        name="X",
        role="strategist",
        system_prompt="x",
        llm_profile_id="llm-x",
    )
    assert a.max_rounds == 5
    assert a.consensus_threshold == 0.9


def test_workflow_template_data_minimal() -> None:
    w = WorkflowTemplateData(id="w1", name="X")
    assert w.category == "system"
    assert w.template_data == {}


def test_language_pack_data_minimal() -> None:
    pack = LanguagePackData(locale="de")
    assert pack.source_locale == "en"
    assert pack.key_count == 0
    assert pack.coverage == 0.0
    assert pack.ui_strings_file == "ui_strings.json"
