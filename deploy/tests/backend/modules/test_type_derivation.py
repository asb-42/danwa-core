"""Tests for backend.modules.type_derivation."""

from __future__ import annotations

from pathlib import Path

from backend.modules.models import ModuleCategory, ModuleType
from backend.modules.type_derivation import (
    derive_module_category,
    derive_module_type,
    parent_dir_name,
    resolve_manifest_type,
)

# ---------------------------------------------------------------------------
# resolve_manifest_type
# ---------------------------------------------------------------------------


def test_resolve_manifest_type_direct() -> None:
    assert resolve_manifest_type("agent-persona") == ModuleType.AGENT_PERSONA


def test_resolve_manifest_type_alias_agent_core() -> None:
    assert resolve_manifest_type("agent-core") == ModuleType.AGENT_PERSONA


def test_resolve_manifest_type_alias_arg_pattern() -> None:
    assert resolve_manifest_type("argumentation-pattern") == ModuleType.ARGUMENTATION_PATTERN


def test_resolve_manifest_type_unknown_returns_none() -> None:
    assert resolve_manifest_type("not-a-real-type") is None


def test_resolve_manifest_type_empty_string() -> None:
    assert resolve_manifest_type("") is None


# ---------------------------------------------------------------------------
# derive_module_type — by parent dir
# ---------------------------------------------------------------------------


def test_derive_module_type_by_dir_llm_profiles() -> None:
    assert derive_module_type("llm-profiles", "anything") == ModuleType.LLM_PROFILE


def test_derive_module_type_by_dir_workflows() -> None:
    assert derive_module_type("workflows", "anything") == ModuleType.WORKFLOW_TEMPLATE


def test_derive_module_type_by_dir_bundles() -> None:
    assert derive_module_type("agent-bundles", "anything") == ModuleType.BUNDLE


def test_derive_module_type_by_dir_kitsune() -> None:
    assert derive_module_type("kitsune-assistant", "anything") == ModuleType.KITSUNE_ASSISTANT


def test_derive_module_type_by_dir_unknown_dir_falls_through() -> None:
    """Unknown dir + UUID prefix → UUID-prefix mapping."""
    assert derive_module_type("unknown", "ac-1234") == ModuleType.AGENT_PERSONA


# ---------------------------------------------------------------------------
# derive_module_type — by UUID prefix
# ---------------------------------------------------------------------------


def test_derive_module_type_by_uuid_prefix_ac() -> None:
    assert derive_module_type("nope", "ac-12345678") == ModuleType.AGENT_PERSONA


def test_derive_module_type_by_uuid_prefix_wt() -> None:
    assert derive_module_type("nope", "wt-12345678") == ModuleType.WORKFLOW_TEMPLATE


def test_derive_module_type_by_uuid_prefix_llm() -> None:
    assert derive_module_type("nope", "llm-12345678") == ModuleType.LLM_PROFILE


def test_derive_module_type_by_uuid_prefix_tp() -> None:
    assert derive_module_type("nope", "tp-12345678") == ModuleType.TONE_PROFILE


def test_derive_module_type_by_uuid_prefix_ap() -> None:
    assert derive_module_type("nope", "ap-12345678") == ModuleType.ARGUMENTATION_PATTERN


def test_derive_module_type_by_uuid_prefix_pm() -> None:
    assert derive_module_type("nope", "pm-12345678") == ModuleType.PROMPT_MODIFIER


def test_derive_module_type_by_uuid_prefix_rt() -> None:
    assert derive_module_type("nope", "rt-12345678") == ModuleType.ROLE_TYPE


def test_derive_module_type_by_uuid_prefix_bd() -> None:
    assert derive_module_type("nope", "bd-12345678") == ModuleType.BUNDLE


def test_derive_module_type_by_uuid_prefix_lp() -> None:
    assert derive_module_type("nope", "lp-12345678") == ModuleType.LANGUAGE_PACK


def test_derive_module_type_by_uuid_prefix_ka() -> None:
    assert derive_module_type("nope", "ka-12345678") == ModuleType.KITSUNE_ASSISTANT


# ---------------------------------------------------------------------------
# derive_module_type — by legacy prefix
# ---------------------------------------------------------------------------


def test_derive_module_type_legacy_agent() -> None:
    assert derive_module_type("nope", "agent-strategist") == ModuleType.AGENT_PERSONA


def test_derive_module_type_legacy_role() -> None:
    assert derive_module_type("nope", "role-default") == ModuleType.ROLE_TYPE


def test_derive_module_type_legacy_tone() -> None:
    assert derive_module_type("nope", "tone-formal") == ModuleType.TONE_PROFILE


def test_derive_module_type_legacy_prompt() -> None:
    assert derive_module_type("nope", "prompt-x") == ModuleType.PROMPT_VARIANT


def test_derive_module_type_legacy_workflow() -> None:
    assert derive_module_type("nope", "workflow-y") == ModuleType.WORKFLOW_TEMPLATE


def test_derive_module_type_legacy_bundle() -> None:
    assert derive_module_type("nope", "bundle-z") == ModuleType.BUNDLE


def test_derive_module_type_legacy_lang() -> None:
    assert derive_module_type("nope", "lang-de") == ModuleType.LANGUAGE_PACK


def test_derive_module_type_legacy_kitsune() -> None:
    assert derive_module_type("nope", "kitsune-x") == ModuleType.KITSUNE_ASSISTANT


# ---------------------------------------------------------------------------
# derive_module_type — fallback
# ---------------------------------------------------------------------------


def test_derive_module_type_fallback_agent_persona() -> None:
    """No match anywhere → fallback to AGENT_PERSONA."""
    assert derive_module_type("nope", "x") == ModuleType.AGENT_PERSONA


# ---------------------------------------------------------------------------
# derive_module_category
# ---------------------------------------------------------------------------


def test_derive_module_category_llm() -> None:
    assert derive_module_category("llm-profiles") == ModuleCategory.LLM_PROFILES


def test_derive_module_category_workflows() -> None:
    assert derive_module_category("workflows") == ModuleCategory.WORKFLOWS


def test_derive_module_category_bundles() -> None:
    assert derive_module_category("agent-bundles") == ModuleCategory.BUNDLES


def test_derive_module_category_prompts() -> None:
    assert derive_module_category("agent-argumentation-patterns") == ModuleCategory.PROMPTS


def test_derive_module_category_translations() -> None:
    assert derive_module_category("ui-translations") == ModuleCategory.TRANSLATIONS


def test_derive_module_category_tone_profiles() -> None:
    assert derive_module_category("agent-tone-profiles") == ModuleCategory.TONE_PROFILES


def test_derive_module_category_unknown_falls_back_to_agents() -> None:
    assert derive_module_category("nope") == ModuleCategory.AGENTS


# ---------------------------------------------------------------------------
# parent_dir_name
# ---------------------------------------------------------------------------


def test_parent_dir_name_one_level() -> None:
    modules = Path("/modules")
    module = Path("/modules/llm-profiles/foo")
    assert parent_dir_name(module, modules) == "llm-profiles"


def test_parent_dir_name_root_level() -> None:
    """A module directly under ``modules/`` → empty string."""
    modules = Path("/modules")
    module = Path("/modules/foo")
    assert parent_dir_name(module, modules) == ""


def test_parent_dir_name_outside_modules() -> None:
    modules = Path("/modules")
    module = Path("/other/foo")
    assert parent_dir_name(module, modules) == ""


def test_parent_dir_name_two_levels() -> None:
    modules = Path("/modules")
    module = Path("/modules/cat/sub/foo")
    assert parent_dir_name(module, modules) == "cat"
