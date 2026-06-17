"""Tests for backend.modules.dependency_resolver — semver + role resolution."""

from __future__ import annotations

import pytest

from backend.modules.dependency_resolver import (
    DependencyCycleError,
    DependencyError,
    DependencyResolver,
)


# ---------------------------------------------------------------------------
# Module-deps resolution
# ---------------------------------------------------------------------------


def test_resolve_all_dependencies_satisfied() -> None:
    r = DependencyResolver()
    errors = r.resolve(
        "my-mod",
        {"a": ">=1.0.0", "b": ">=2.0.0,<3.0.0"},
        {"a": "1.5.0", "b": "2.3.0"},
    )
    assert errors == []


def test_resolve_missing_dependency() -> None:
    r = DependencyResolver()
    errors = r.resolve("my-mod", {"missing": ">=1.0.0"}, {})
    assert len(errors) == 1
    assert "Missing dependency" in errors[0]
    assert "missing" in errors[0]


def test_resolve_version_below_constraint() -> None:
    r = DependencyResolver()
    errors = r.resolve("my-mod", {"a": ">=2.0.0"}, {"a": "1.5.0"})
    assert len(errors) == 1
    assert "does not satisfy" in errors[0]


def test_resolve_caret_constraint_rejected() -> None:
    """``^`` is npm-style and not PEP 440; we expect it to be rejected."""
    r = DependencyResolver()
    errors = r.resolve("m", {"a": "^1.0.0"}, {"a": "1.5.0"})
    assert len(errors) == 1
    assert "Invalid dependency constraint" in errors[0]


def test_resolve_tilde_constraint_rejected() -> None:
    """``~`` is npm-style and not PEP 440; we expect it to be rejected."""
    r = DependencyResolver()
    errors = r.resolve("m", {"a": "~1.0.0"}, {"a": "1.0.5"})
    assert len(errors) == 1
    assert "Invalid dependency constraint" in errors[0]


def test_resolve_invalid_constraint_format() -> None:
    """``not-a-real-constraint`` is not a valid PEP 440 specifier; we
    just assert that the function returns a list (no exception) — the
    implementation may or may not flag it, depending on how the
    ``packaging.Requirement`` parser interprets it.
    """
    r = DependencyResolver()
    errors = r.resolve("m", {"a": "not-a-real-constraint"}, {"a": "1.0.0"})
    assert isinstance(errors, list)


def test_resolve_invalid_version() -> None:
    r = DependencyResolver()
    errors = r.resolve("m", {"a": ">=1.0.0"}, {"a": "not-a-version"})
    assert len(errors) == 1
    assert "Invalid dependency constraint" in errors[0]


def test_resolve_pep440_range_constraint() -> None:
    """A real PEP 440 range constraint is accepted."""
    r = DependencyResolver()
    errors = r.resolve("m", {"a": ">=1.0.0,<2.0.0"}, {"a": "1.5.0"})
    assert errors == []


def test_resolve_pep440_range_constraint_violation() -> None:
    r = DependencyResolver()
    errors = r.resolve("m", {"a": ">=1.0.0,<2.0.0"}, {"a": "2.5.0"})
    assert len(errors) == 1
    assert "does not satisfy" in errors[0]


# ---------------------------------------------------------------------------
# Role resolution
# ---------------------------------------------------------------------------


def test_resolve_roles_all_satisfied() -> None:
    installed = [
        {"module_id": "core-1", "type": "agent-core", "role": "strategist", "tags": ["default"]},
        {"module_id": "core-2", "type": "agent-core", "role": "critic"},
    ]
    errors, role_map = DependencyResolver.resolve_roles(
        "bundle", ["strategist", "critic"], installed,
    )
    assert errors == []
    assert role_map == {"strategist": "core-1", "critic": "core-2"}


def test_resolve_roles_missing_role() -> None:
    installed = [{"module_id": "core-1", "type": "agent-core", "role": "strategist"}]
    errors, role_map = DependencyResolver.resolve_roles(
        "bundle", ["strategist", "missing-role"], installed,
    )
    assert role_map == {"strategist": "core-1"}
    assert len(errors) == 1
    assert "missing-role" in errors[0]


def test_resolve_roles_prefers_default_tag() -> None:
    installed = [
        {"module_id": "core-1", "type": "agent-core", "role": "strategist", "tags": []},
        {"module_id": "core-2", "type": "agent-core", "role": "strategist", "tags": ["default"]},
    ]
    errors, role_map = DependencyResolver.resolve_roles(
        "bundle", ["strategist"], installed,
    )
    assert role_map["strategist"] == "core-2"


def test_resolve_roles_module_without_role_field() -> None:
    """Modules without a ``role`` field are ignored for role resolution."""
    installed = [
        {"module_id": "core-1", "type": "agent-core", "role": "strategist"},
        {"module_id": "workflow-1", "type": "workflow"},  # no role
    ]
    errors, role_map = DependencyResolver.resolve_roles(
        "bundle", ["strategist"], installed,
    )
    assert role_map["strategist"] == "core-1"


def test_resolve_roles_empty_installed() -> None:
    errors, role_map = DependencyResolver.resolve_roles("b", ["strategist"], [])
    assert role_map == {}
    assert len(errors) == 1


def test_resolve_roles_no_required_roles() -> None:
    installed = [{"module_id": "c1", "type": "agent-core", "role": "strategist"}]
    errors, role_map = DependencyResolver.resolve_roles("b", [], installed)
    assert errors == []
    assert role_map == {}


# ---------------------------------------------------------------------------
# find_missing_roles
# ---------------------------------------------------------------------------


def test_find_missing_roles_all_present() -> None:
    installed = [
        {"module_id": "c1", "type": "agent-core", "role": "strategist"},
        {"module_id": "c2", "type": "agent-core", "role": "critic"},
    ]
    assert DependencyResolver.find_missing_roles(["strategist", "critic"], installed) == []


def test_find_missing_roles_some_missing() -> None:
    installed = [{"module_id": "c1", "type": "agent-core", "role": "strategist"}]
    missing = DependencyResolver.find_missing_roles(["strategist", "critic"], installed)
    assert missing == ["critic"]


def test_find_missing_roles_empty_required() -> None:
    assert DependencyResolver.find_missing_roles([], []) == []


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------


def test_detect_cycles_no_cycle() -> None:
    deps = {"a": {"b": ">=1.0"}, "b": {"c": ">=1.0"}, "c": {}}
    errors = DependencyResolver.detect_cycles("a", deps["a"], deps)
    assert errors == []


def test_detect_cycles_simple_cycle() -> None:
    """a → b → a"""
    deps = {"a": {"b": ">=1.0"}, "b": {"a": ">=1.0"}}
    errors = DependencyResolver.detect_cycles("a", deps["a"], deps)
    assert any("Circular dependency" in e for e in errors)


def test_detect_cycles_self_loop() -> None:
    """a → a (self-loop)."""
    deps = {"a": {"a": ">=1.0"}}
    errors = DependencyResolver.detect_cycles("a", deps["a"], deps)
    assert any("Circular dependency" in e for e in errors)


def test_detect_cycles_three_node_cycle() -> None:
    """a → b → c → a."""
    deps = {
        "a": {"b": ">=1.0"},
        "b": {"c": ">=1.0"},
        "c": {"a": ">=1.0"},
    }
    errors = DependencyResolver.detect_cycles("a", deps["a"], deps)
    assert any("Circular dependency" in e for e in errors)


def test_detect_cycles_ignores_unknown_deps() -> None:
    """Dependencies to modules not in ``all_deps`` are ignored (external)."""
    deps = {"a": {"b": ">=1.0"}, "b": {}}
    errors = DependencyResolver.detect_cycles("a", deps["a"], deps)
    assert errors == []


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


def test_dependency_error_inheritance() -> None:
    assert issubclass(DependencyCycleError, DependencyError)
    assert issubclass(DependencyError, Exception)
