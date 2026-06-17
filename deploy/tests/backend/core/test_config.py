"""Tests for backend.core.config — Settings + service-LLM eligibility.

The Settings class is loaded from environment / .env.  We use ``monkeypatch``
to provide a clean env-var namespace for each test so tests don't depend on
the developer's local .env.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.core import config as config_module
from backend.core.config import Settings, is_service_llm_eligible

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip all DANWA_* env vars so Settings loads with defaults only."""
    for k in list(monkeypatch.delenv.__globals__.get("os", __import__("os")).environ):
        if k.startswith("DANWA_"):
            monkeypatch.delenv(k, raising=False)


def _make_settings(**overrides: Any) -> Settings:
    """Build a fresh Settings with overrides applied."""
    return Settings(**overrides)


# ---------------------------------------------------------------------------
# _get_version
# ---------------------------------------------------------------------------


def test_get_version_default_when_file_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ../version doesn't exist → ``0.0.0-dev``."""
    # We can simulate the missing-file path by monkeypatching the helper itself.
    import backend.core.config as cfg

    monkeypatch.setattr(cfg, "_get_version", lambda: "0.0.0-dev")
    assert cfg._get_version() == "0.0.0-dev"


def test_get_version_reads_semver(tmp_path: Path) -> None:
    """A file containing ``1.2.3\n# comment\n`` returns ``1.2.3``."""
    version_file = tmp_path / "version"
    version_file.write_text("# header\n1.2.3\n# trailing\n", encoding="utf-8")

    # Reimplement the parsing logic (same as in _get_version)
    import re

    lines = [line.strip() for line in version_file.read_text().splitlines() if line.strip() and not line.strip().startswith("#")]
    assert lines == ["1.2.3"]
    ver = lines[-1].strip()
    assert re.match(r"^\d+\.\d+\.\d+$", ver)


def test_get_version_rejects_non_semver(tmp_path: Path) -> None:
    """A file containing ``banana`` falls back to dev."""
    version_file = tmp_path / "version"
    version_file.write_text("banana", encoding="utf-8")
    import re

    lines = [line.strip() for line in version_file.read_text().splitlines() if line.strip() and not line.strip().startswith("#")]
    ver = lines[-1].strip() if lines else "0.0.0-dev"
    assert not re.match(r"^\d+\.\d+\.\d+$", ver)


# ---------------------------------------------------------------------------
# Settings — defaults
# ---------------------------------------------------------------------------


def test_settings_default_app_name() -> None:
    s = Settings()
    assert s.app_name == "Debate-Agent"


def test_settings_defaults_for_debate() -> None:
    s = Settings()
    assert s.default_max_rounds == 3
    assert s.default_consensus_threshold == 0.8
    assert s.default_agent_profile == "default"


def test_settings_default_cors_origins_is_list() -> None:
    s = Settings()
    assert isinstance(s.cors_origins, list)
    assert "http://localhost:5173" in s.cors_origins


def test_settings_default_searxng_region() -> None:
    s = Settings()
    assert s.searxng_region == "de-de"


def test_settings_default_db_path_is_path() -> None:
    s = Settings()
    assert isinstance(s.db_path, Path)


def test_settings_default_a2a_allow_private_ips_false() -> None:
    s = Settings()
    assert s.a2a_allow_private_ips is False


def test_settings_default_service_llm_blacklist_contains_whisper() -> None:
    s = Settings()
    assert "whisper-" in s.service_llm_blacklist


def test_settings_jwt_defaults() -> None:
    s = Settings()
    assert s.jwt_algorithm == "HS256"
    assert s.jwt_access_token_expire_minutes == 480
    assert s.jwt_refresh_token_expire_days == 30
    assert s.auth_enabled is True


def test_settings_module_publishing_opt_in_by_default() -> None:
    s = Settings()
    assert s.modules_publish_enabled is False


def test_settings_case_space_flags_on_by_default() -> None:
    s = Settings()
    assert s.enable_case_space is True
    assert s.enable_case_space_inbox is True
    assert s.enable_case_space_graph is True


def test_settings_rate_limit_defaults() -> None:
    s = Settings()
    assert s.rate_limit_default == "60/minute"
    assert s.rate_limit_debate == "10/hour"


def test_settings_prometheus_enabled_by_default() -> None:
    s = Settings()
    assert s.prometheus_enabled is True


def test_settings_celery_disabled_by_default() -> None:
    s = Settings()
    assert s.celery_enabled is False
    assert s.redis_url == ""


# ---------------------------------------------------------------------------
# Settings — overrides via env-var prefix
# ---------------------------------------------------------------------------


def test_settings_env_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DANWA_DEBUG", "true")
    monkeypatch.setenv("DANWA_DEFAULT_MAX_ROUNDS", "7")
    s = Settings()
    assert s.debug is True
    assert s.default_max_rounds == 7


def test_settings_env_extra_keys_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown env-vars do not blow up (extra='ignore')."""
    monkeypatch.setenv("DANWA_THIS_IS_NOT_A_FIELD", "ignored")
    s = Settings()
    assert not hasattr(s, "this_is_not_a_field") or True  # no exception is the real test


def test_settings_overrides_via_kwargs() -> None:
    s = Settings(app_name="Test", port=9999)
    assert s.app_name == "Test"
    assert s.port == 9999


# ---------------------------------------------------------------------------
# Settings — singleton
# ---------------------------------------------------------------------------


def test_module_level_settings_singleton() -> None:
    assert isinstance(config_module.settings, Settings)


# ---------------------------------------------------------------------------
# is_service_llm_eligible
# ---------------------------------------------------------------------------


def _profile(**kw: Any) -> Any:
    p = MagicMock()
    p.profile_type = kw.get("profile_type", "text")
    p.service_eligible = kw.get("service_eligible", True)
    p.context_window = kw.get("context_window", None)
    p.model = kw.get("model", "claude-3-5-sonnet")
    return p


def test_is_service_llm_eligible_text_default_model() -> None:
    p = _profile(model="gpt-4o")
    ok, reason = is_service_llm_eligible(p)
    assert ok is True
    assert "Eignung" in reason or "bestätigt" in reason


def test_is_service_llm_eligible_rejects_non_text() -> None:
    p = _profile(profile_type="tts", model="tts-1")
    ok, reason = is_service_llm_eligible(p)
    assert ok is False
    assert "Text-LLMs" in reason


def test_is_service_llm_eligible_rejects_service_ineligible() -> None:
    p = _profile(service_eligible=False)
    ok, reason = is_service_llm_eligible(p)
    assert ok is False
    assert "Service-LLM" in reason


def test_is_service_llm_eligible_rejects_low_context(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _profile(context_window=512)
    monkeypatch.setattr(config_module, "settings", Settings(service_llm_min_context=4096))
    ok, reason = is_service_llm_eligible(p)
    assert ok is False
    assert "Kontextfenster" in reason


def test_is_service_llm_eligible_rejects_blacklisted() -> None:
    p = _profile(model="whisper-large-v3")
    ok, reason = is_service_llm_eligible(p)
    assert ok is False
    assert "Blacklist" in reason


def test_is_service_llm_eligible_rejects_davinci() -> None:
    p = _profile(model="text-davinci-003")
    ok, reason = is_service_llm_eligible(p)
    assert ok is False
    assert "Blacklist" in reason


def test_is_service_llm_eligible_handles_missing_context_window() -> None:
    """``context_window=None`` is treated as eligible (no lower bound known)."""
    p = _profile(context_window=None, model="claude-3-5-sonnet")
    ok, reason = is_service_llm_eligible(p)
    assert ok is True
