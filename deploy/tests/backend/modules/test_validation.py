"""Tests for backend.modules.validation — ModuleValidator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.modules.validation import ModuleValidator


@pytest.fixture
def validator(tmp_path: Path) -> ModuleValidator:
    return ModuleValidator(module_base_dir=tmp_path)


def _write_manifest(modules_dir: Path, module_id: str, manifest: dict) -> None:
    p = modules_dir / module_id / "manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest), encoding="utf-8")


def _minimal_v1_manifest() -> dict:
    return {
        "schema_version": "1.0.0",
        "module_id": "my-mod",
        "name": {"en": "My Module"},
        "version": "1.0.0",
        "type": "agent-persona",
        "category": "agents",
        "files": [
            {"path": "profile.md", "format": "markdown"},
        ],
    }


def _minimal_v2_manifest() -> dict:
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


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_validate_v1_minimal_manifest_ok(validator: ModuleValidator) -> None:
    result = validator.validate_manifest(_minimal_v1_manifest())
    errors = [i for i in result.issues if i.severity == "error"]
    assert errors == []


def test_validate_v2_minimal_manifest_ok(validator: ModuleValidator) -> None:
    result = validator.validate_manifest(_minimal_v2_manifest())
    errors = [i for i in result.issues if i.severity == "error"]
    assert errors == []


def test_validate_v3_manifest_ok(validator: ModuleValidator) -> None:
    manifest = _minimal_v2_manifest()
    manifest["schema_version"] = "3.0.0"
    manifest["compatibility"] = {"danwa_min_version": "1.0.0", "danwa_max_version": "2.0.0"}
    manifest["repository"] = {"type": "github", "url": "https://example.com", "ref": "main"}
    result = validator.validate_manifest(manifest)
    errors = [i for i in result.issues if i.severity == "error"]
    assert errors == []


# ---------------------------------------------------------------------------
# module_id validation
# ---------------------------------------------------------------------------


def test_missing_module_id_is_error(validator: ModuleValidator) -> None:
    m = _minimal_v1_manifest()
    m["module_id"] = ""
    result = validator.validate_manifest(m)
    assert any("module_id" in i.field and i.severity == "error" for i in result.issues)


def test_invalid_module_id_uppercase_is_error(validator: ModuleValidator) -> None:
    m = _minimal_v1_manifest()
    m["module_id"] = "INVALID"
    result = validator.validate_manifest(m)
    assert any("module_id" in i.field for i in result.issues)


def test_invalid_module_id_special_chars_is_error(validator: ModuleValidator) -> None:
    m = _minimal_v1_manifest()
    m["module_id"] = "bad/id"
    result = validator.validate_manifest(m)
    assert any(i.severity == "error" for i in result.issues if "module_id" in i.field)


# ---------------------------------------------------------------------------
# Version validation
# ---------------------------------------------------------------------------


def test_invalid_version_format_is_error(validator: ModuleValidator) -> None:
    m = _minimal_v1_manifest()
    m["version"] = "1.0"
    result = validator.validate_manifest(m)
    assert any("version" in i.field and i.severity == "error" for i in result.issues)


def test_invalid_version_banana_is_error(validator: ModuleValidator) -> None:
    m = _minimal_v1_manifest()
    m["version"] = "banana"
    result = validator.validate_manifest(m)
    assert any("version" in i.field for i in result.issues)


# ---------------------------------------------------------------------------
# Required fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["name", "version", "type", "category"])
def test_missing_required_field_is_error(validator: ModuleValidator, field: str) -> None:
    m = _minimal_v1_manifest()
    del m[field]
    result = validator.validate_manifest(m)
    assert any(i.field == field and i.severity == "error" for i in result.issues)


# ---------------------------------------------------------------------------
# Type / category
# ---------------------------------------------------------------------------


def test_invalid_type_is_error(validator: ModuleValidator) -> None:
    m = _minimal_v1_manifest()
    m["type"] = "not-a-real-type"
    result = validator.validate_manifest(m)
    assert any("type" in i.field and i.severity == "error" for i in result.issues)


def test_invalid_category_is_error(validator: ModuleValidator) -> None:
    m = _minimal_v1_manifest()
    m["category"] = "not-a-real-category"
    result = validator.validate_manifest(m)
    assert any("category" in i.field and i.severity == "error" for i in result.issues)


# ---------------------------------------------------------------------------
# files / profile_file
# ---------------------------------------------------------------------------


def test_v1_without_files_is_error(validator: ModuleValidator) -> None:
    m = _minimal_v1_manifest()
    m["files"] = []
    result = validator.validate_manifest(m)
    assert any("files" in i.field and i.severity == "error" for i in result.issues)


def test_v2_without_profile_file_is_error(validator: ModuleValidator) -> None:
    m = _minimal_v2_manifest()
    del m["profile_file"]
    result = validator.validate_manifest(m)
    assert any("profile_file" in i.field and i.severity == "error" for i in result.issues)


def test_duplicate_file_paths_is_error(validator: ModuleValidator) -> None:
    m = _minimal_v1_manifest()
    m["files"] = [
        {"path": "profile.md", "format": "markdown"},
        {"path": "profile.md", "format": "yaml"},
    ]
    result = validator.validate_manifest(m)
    assert any("Duplicate file path" in i.message for i in result.issues)


def test_unusual_file_format_is_warning(validator: ModuleValidator) -> None:
    m = _minimal_v1_manifest()
    m["files"] = [{"path": "data.bin", "format": "binary"}]
    result = validator.validate_manifest(m)
    assert any("format" in i.field and i.severity == "warning" for i in result.issues)


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------


def test_unknown_schema_version_is_warning(validator: ModuleValidator) -> None:
    m = _minimal_v1_manifest()
    m["schema_version"] = "99.0.0"
    result = validator.validate_manifest(m)
    assert any("schema_version" in i.field and i.severity == "warning" for i in result.issues)


def test_v3_invalid_danwa_min_version_is_warning(validator: ModuleValidator) -> None:
    m = _minimal_v2_manifest()
    m["schema_version"] = "3.0.0"
    m["compatibility"] = {"danwa_min_version": "banana"}
    result = validator.validate_manifest(m)
    assert any("danwa_min_version" in i.field for i in result.issues)


def test_v3_invalid_repository_type_is_warning(validator: ModuleValidator) -> None:
    m = _minimal_v2_manifest()
    m["schema_version"] = "3.0.0"
    m["repository"] = {"type": "bitbucket", "url": "https://example.com"}
    result = validator.validate_manifest(m)
    assert any("repository.type" in i.field for i in result.issues)
