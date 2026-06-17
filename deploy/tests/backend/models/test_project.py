"""Tests for backend.models.project — Project and ProjectConfig."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.core.profiles import LLMProfile, LLMProvider
from backend.models.project import Project, ProjectConfig


def test_project_defaults() -> None:
    p = Project(name="My Project")
    assert p.tenant_id == "_default"
    assert p.is_system is False
    assert p.config == ProjectConfig()


def test_project_empty_name_rejected() -> None:
    with pytest.raises(ValidationError):
        Project(name="")


def test_project_long_name_rejected() -> None:
    with pytest.raises(ValidationError):
        Project(name="x" * 201)


def test_project_unique_ids() -> None:
    a = Project(name="A")
    b = Project(name="B")
    assert a.id != b.id


def test_project_config_defaults() -> None:
    c = ProjectConfig()
    assert c.language is None
    assert c.default_max_rounds is None
    assert c.default_consensus_threshold is None
    assert c.search_mode is None
    assert c.llm_profiles == {}


def test_project_config_with_overrides() -> None:
    prof = LLMProfile(name="X", provider=LLMProvider.OPENAI, model="gpt-4o")
    c = ProjectConfig(
        language="de",
        default_max_rounds=7,
        search_mode="optional",
        llm_profiles={"x": prof},
    )
    assert c.language == "de"
    assert c.default_max_rounds == 7
    assert c.llm_profiles["x"].model == "gpt-4o"


def test_project_config_dump_round_trip() -> None:
    c = ProjectConfig(language="en", default_max_rounds=5)
    c2 = ProjectConfig(**c.model_dump())
    assert c2.language == "en"
    assert c2.default_max_rounds == 5
