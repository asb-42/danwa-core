"""Catalog normalizer — turn per-source JSON into a uniform shape.

Two public functions:

- :func:`normalize_catwalk` parses one ``<catwalk>/<provider>.json``
  file and yields one :class:`NormalizedModel` per entry.
- :func:`normalize_llm_db` parses one ``<llm_db>/<provider>.json``
  file the same way.

Both produce a uniform dict shape that matches the new optional
fields on :class:`backend.blueprints.models.BlueprintLLMProfile`
(Sprint 7 additions).  A higher-level :func:`load_all_normalized`
walks a fetched source tree and returns the merged list.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path
from typing import Any, Iterator

from backend.llm_catalog.sources import SourceSpec

logger = logging.getLogger(__name__)


# ─── Normalized model shape ──────────────────────────────────────────


@dataclasses.dataclass
class NormalizedModel:
    """One provider + model tuple, source-agnostic."""

    source: str                  # "catwalk" | "llm_db"
    provider: str                 # the file stem, e.g. "openai"
    provider_name: str            # human-readable provider name
    catalog_id: str               # the model id as the catalog spells it
    name: str | None
    api_base: str | None
    api_key_env: str | None
    context_window: int | None
    max_tokens: int | None
    # Cost (per-1M, USD, source-native)
    cost_per_1m_input: float | None
    cost_per_1m_output: float | None
    cost_per_1m_cached_input: float | None
    cost_per_1m_cached_output: float | None
    # Reasoning
    can_reason: bool
    reasoning_levels: list[str]
    default_reasoning_effort: str | None
    # Capabilities & modalities
    capabilities: dict[str, Any]
    modalities: dict[str, list[str]]
    # Lifecycle / metadata
    lifecycle_status: str | None
    knowledge_cutoff: str | None
    release_date: str | None
    last_updated: str | None
    family: str | None
    aliases: list[str]
    tags: list[str]
    # Provider-level
    api_endpoint_template: str | None
    default_large_model_id: str | None
    default_small_model_id: str | None
    # Provenance
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# ─── catwalk ────────────────────────────────────────────────────────


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f >= 0 else None


def _safe_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _safe_bool(v: Any) -> bool:
    return bool(v) if v is not None else False


def _clean_env_token(s: str | None) -> str | None:
    """Turn ``$OPENAI_API_KEY`` into ``OPENAI_API_KEY`` for our field."""
    if not s:
        return None
    s = s.strip()
    if s.startswith("$") and len(s) > 1 and s[1].isalpha():
        return s[1:]
    return s


def normalize_catwalk(
    provider_file: Path,
    provider_id: str,
    provider_name: str,
    raw: dict[str, Any],
) -> Iterator[NormalizedModel]:
    """Yield one :class:`NormalizedModel` per catwalk model entry.

    catwalk's per-provider JSON is a flat dict whose ``models`` field
    is a list of model dicts.  The file stem is the canonical
    provider id (matches our existing ``provider`` enum).
    """
    api_key_template = _clean_env_token(raw.get("api_key"))
    api_endpoint = raw.get("api_endpoint")
    default_large = raw.get("default_large_model_id")
    default_small = raw.get("default_small_model_id")
    models = raw.get("models") or []
    if not isinstance(models, list):
        return
    for m in models:
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        if not isinstance(mid, str) or not mid:
            continue
        yield NormalizedModel(
            source="catwalk",
            provider=provider_id,
            provider_name=provider_name,
            catalog_id=mid,
            name=m.get("name"),
            api_base=api_endpoint,
            api_key_env=api_key_template,
            context_window=_safe_int(m.get("context_window")),
            max_tokens=_safe_int(m.get("default_max_tokens")),
            cost_per_1m_input=_safe_float(m.get("cost_per_1m_in")),
            cost_per_1m_output=_safe_float(m.get("cost_per_1m_out")),
            cost_per_1m_cached_input=_safe_float(m.get("cost_per_1m_in_cached")),
            cost_per_1m_cached_output=_safe_float(m.get("cost_per_1m_out_cached")),
            can_reason=_safe_bool(m.get("can_reason")),
            reasoning_levels=list(m.get("reasoning_levels") or []),
            default_reasoning_effort=m.get("default_reasoning_effort"),
            capabilities={"supports_attachments": _safe_bool(m.get("supports_attachments"))},
            modalities={},
            lifecycle_status=None,
            knowledge_cutoff=None,
            release_date=None,
            last_updated=None,
            family=None,
            aliases=[],
            tags=[],
            api_endpoint_template=api_endpoint,
            default_large_model_id=default_large,
            default_small_model_id=default_small,
            raw=m,
        )


# ─── llm_db ──────────────────────────────────────────────────────────


def normalize_llm_db(
    provider_file: Path,
    provider_id: str,
    provider_name: str,
    raw: dict[str, Any],
) -> Iterator[NormalizedModel]:
    """Yield one :class:`NormalizedModel` per llm_db model entry.

    llm_db's per-provider JSON has ``models`` as a ``{model_id: {...}}``
    dict.  The file stem is the canonical provider id; we use the
    model dict's ``provider`` field for cross-checking.
    """
    base_url = raw.get("base_url")
    env_token = None
    env_list = raw.get("env")
    if isinstance(env_list, list) and env_list:
        # Use the first env var name as the default api_key_env
        env_token = str(env_list[0])
    models = raw.get("models") or {}
    if not isinstance(models, dict):
        return
    for mid, m in models.items():
        if not isinstance(mid, str) or not mid:
            continue
        if not isinstance(m, dict):
            continue
        caps = m.get("capabilities") or {}
        cost = m.get("cost") or {}
        limits = m.get("limits") or {}
        modalities = m.get("modalities") or {}
        # cost in llm_db is per-1K (not per-1M), multiply by 1000
        # to match catwalk's per-1M convention.
        cost_in = _safe_float(cost.get("input"))
        cost_out = _safe_float(cost.get("output"))
        cost_cache_in = _safe_float(cost.get("cache_read"))
        cost_cache_out = _safe_float(cost.get("cache_write"))
        # Reasoning lives under capabilities.reasoning.enabled
        reasoning = caps.get("reasoning") or {}
        can_reason = _safe_bool(reasoning.get("enabled"))
        yield NormalizedModel(
            source="llm_db",
            provider=provider_id,
            provider_name=provider_name,
            catalog_id=mid,
            name=m.get("name"),
            api_base=base_url,
            api_key_env=env_token,
            context_window=_safe_int(limits.get("context")),
            max_tokens=_safe_int(limits.get("output")),
            cost_per_1m_input=cost_in * 1000 if cost_in is not None else None,
            cost_per_1m_output=cost_out * 1000 if cost_out is not None else None,
            cost_per_1m_cached_input=cost_cache_in * 1000 if cost_cache_in is not None else None,
            cost_per_1m_cached_output=cost_cache_out * 1000 if cost_cache_out is not None else None,
            can_reason=can_reason,
            reasoning_levels=[],
            default_reasoning_effort=None,
            capabilities=caps if isinstance(caps, dict) else {},
            modalities=modalities if isinstance(modalities, dict) else {},
            lifecycle_status=((m.get("lifecycle") or {}) or {}).get("status"),
            knowledge_cutoff=m.get("knowledge"),
            release_date=m.get("release_date"),
            last_updated=m.get("last_updated"),
            family=m.get("family"),
            aliases=list(m.get("aliases") or []),
            tags=list(m.get("tags") or []),
            api_endpoint_template=None,
            default_large_model_id=None,
            default_small_model_id=None,
            raw=m,
        )


# ─── Loader ──────────────────────────────────────────────────────────


def _is_safe_provider_filename(name: str) -> bool:
    """Defence in depth: refuse path traversal in catalog JSON files."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return False
    return name.endswith(".json")


def load_source_normalized(source: SourceSpec, cache_dir: Path) -> list[NormalizedModel]:
    """Walk the fetched ``<source>/<path>`` dir and normalize all JSON files.

    Failures on individual files are logged and skipped so a single
    malformed file doesn't break the whole import.
    """
    base = cache_dir / source.name / source.path
    if not base.is_dir():
        logger.warning("catalog path missing for %s: %s", source.name, base)
        return []
    out: list[NormalizedModel] = []
    normalizer = normalize_catwalk if source.name == "catwalk" else normalize_llm_db
    for path in sorted(base.glob("*.json")):
        if not _is_safe_provider_filename(path.name):
            logger.warning("skipping suspicious catalog file: %s", path.name)
            continue
        provider_id = path.stem
        try:
            with path.open(encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("skipping %s: %s", path.name, e)
            continue
        if not isinstance(raw, dict):
            continue
        provider_name = str(raw.get("name") or provider_id)
        try:
            count_before = len(out)
            for nm in normalizer(path, provider_id, provider_name, raw):
                out.append(nm)
            logger.debug("normalized %d models from %s", len(out) - count_before, path.name)
        except Exception as e:  # noqa: BLE001
            logger.warning("error normalising %s: %s", path.name, e)
    return out


def load_all_normalized(sources: list[SourceSpec], cache_dir: Path) -> dict[str, list[NormalizedModel]]:
    """Return ``{source_name: [NormalizedModel, ...]}`` for every source."""
    return {s.name: load_source_normalized(s, cache_dir) for s in sources}
