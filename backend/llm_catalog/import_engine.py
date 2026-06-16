"""Import engine — diff local vs catalog and (optionally) apply upsert.

Workflow
--------
1. Walk the catalog tree (already normalized via
   :mod:`backend.llm_catalog.normalize`).
2. For each (source, provider, model) tuple, compute the deterministic
   module id via :mod:`backend.llm_catalog.id_strategy`.
3. Check whether a directory with that id already exists under
   ``<MODULES_DIR>/llm-profiles/llm-<id>/``.  If so, mark it for
   "update"; otherwise for "create".
4. If a catalog id appears locally but NOT upstream, mark it "stale"
   (kept on disk; a separate cleanup pass could remove after N days).
5. ``apply_import(diff)`` writes/updates ``manifest.json`` and
   ``profile.yaml`` for each new/changed entry.  Uses PyYAML for
   the profile.

This module is intentionally separated from :mod:`normalize` so
the diff can be computed without touching the filesystem (``dry_run=True``)
and the apply can be re-run idempotently.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from backend.core.config import Settings
from backend.llm_catalog.id_strategy import (
    display_name,
    module_id_for_provider_model,
)
from backend.llm_catalog.normalize import NormalizedModel
from backend.llm_catalog.sources import (
    SourceSpec,
    list_sources,
    resolve_cache_root,
)

logger = logging.getLogger(__name__)


# ─── Result types ────────────────────────────────────────────────────


@dataclasses.dataclass
class DiffEntry:
    """One row in the import diff."""

    module_id: str
    source: str
    provider: str
    provider_name: str
    catalog_id: str
    name: str
    action: str                 # "create" | "update" | "stale" | "skip"
    reason: str = ""
    changes: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class ImportReport:
    """Outcome of an import run."""

    entries: list[DiffEntry]
    by_action: dict[str, int]
    dry_run: bool
    created_at: str
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "by_action": self.by_action,
            "dry_run": self.dry_run,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


# ─── Settings shortcut ─────────────────────────────────────────────


def _llm_profiles_dir(settings: Settings) -> Path:
    """Absolute path to ``<MODULES_DIR>/llm-profiles/``."""
    # The catalog integration writes into the same dir the existing
    # ``danwa-modules`` consumes.  We honour the same root that
    # ``backend.modules.service`` uses (ROOT/modules).
    from backend.modules.service import MODULES_DIR  # local import to avoid cycle at module load

    return Path(MODULES_DIR) / "llm-profiles"


# ─── Merge: catwalk (priority for cost+reasoning) over llm_db ──────


def _merge_models(models: list[NormalizedModel]) -> dict[str, Any] | None:
    """Merge multiple NormalizedModels for the same (provider, catalog_id).

    Priority: catwalk wins on cost (per-1M) and reasoning; llm_db wins
    on capabilities and modalities.  First-non-null wins for the rest.
    """
    if not models:
        return None
    by_source = {m.source: m for m in models}
    primary = by_source.get("catwalk") or by_source.get("llm_db") or models[0]
    secondary = by_source.get("llm_db") if primary.source == "catwalk" else (
        by_source.get("catwalk") if primary.source == "llm_db" else None
    )

    merged: dict[str, Any] = {
        "source": primary.source,
        "provider": primary.provider,
        "provider_name": primary.provider_name,
        "catalog_id": primary.catalog_id,
    }
    # First-non-null for simple fields
    for field in (
        "name",
        "api_base",
        "api_key_env",
        "context_window",
        "max_tokens",
        "default_reasoning_effort",
        "lifecycle_status",
        "knowledge_cutoff",
        "release_date",
        "last_updated",
        "family",
        "api_endpoint_template",
        "default_large_model_id",
        "default_small_model_id",
    ):
        v = getattr(primary, field, None)
        if v is None and secondary is not None:
            v = getattr(secondary, field, None)
        if v is not None:
            merged[field] = v
    # Cost (per-1M) — catwalk wins
    for field in (
        "cost_per_1m_input",
        "cost_per_1m_output",
        "cost_per_1m_cached_input",
        "cost_per_1m_cached_output",
    ):
        v = getattr(primary, field)
        if v is None and secondary is not None:
            v = getattr(secondary, field)
        if v is not None:
            merged[field] = v
    # Reasoning — catwalk wins
    merged["can_reason"] = bool(primary.can_reason) or bool(secondary.can_reason) if secondary else primary.can_reason
    merged["reasoning_levels"] = list(primary.reasoning_levels) or (
        list(secondary.reasoning_levels) if secondary else []
    )
    # Capabilities / modalities — llm_db preferred if present
    if secondary is not None and secondary.capabilities:
        merged["capabilities"] = secondary.capabilities
    elif primary.capabilities:
        merged["capabilities"] = primary.capabilities
    if secondary is not None and secondary.modalities:
        merged["modalities"] = secondary.modalities
    elif primary.modalities:
        merged["modalities"] = primary.modalities
    # Aliases / tags — concatenate unique
    aliases = set(primary.aliases) | (set(secondary.aliases) if secondary else set())
    tags = set(primary.tags) | (set(secondary.tags) if secondary else set())
    if aliases:
        merged["aliases"] = sorted(aliases)
    if tags:
        merged["catalog_tags"] = sorted(tags)
    if secondary is not None:
        merged["merged_from"] = sorted({primary.source, secondary.source})
    return merged


# ─── Diff ────────────────────────────────────────────────────────────


def _read_yaml(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _read_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _diff_entry(
    merged: dict[str, Any],
    existing_profile: dict[str, Any] | None,
    existing_manifest: dict[str, Any] | None,
) -> tuple[str, list[str]]:
    """Return (action, changes) for one merged model vs existing local."""
    changes: list[str] = []
    if existing_profile is None:
        return "create", ["new module (no local profile)"]
    # Compare key fields
    fields = (
        "name",
        "api_base",
        "api_key_env",
        "context_window",
        "max_tokens",
        "cost_per_1m_input",
        "cost_per_1m_output",
        "cost_per_1m_cached_input",
        "cost_per_1m_cached_output",
        "can_reason",
        "reasoning_levels",
        "capabilities",
        "modalities",
        "lifecycle_status",
        "knowledge_cutoff",
        "release_date",
        "family",
        "aliases",
        "catalog_tags",
    )
    for f in fields:
        old = existing_profile.get(f)
        new = merged.get(f)
        if old != new:
            changes.append(f"{f}: {old!r} -> {new!r}")
    return ("update" if changes else "skip"), changes


def build_diff(
    catalog_models: list[NormalizedModel],
    settings: Settings,
) -> ImportReport:
    """Compute what *would* change without touching disk."""
    profiles_dir = _llm_profiles_dir(settings)
    profiles_dir.mkdir(parents=True, exist_ok=True)

    # Bucket by (provider, catalog_id) so multi-source entries merge
    by_key: dict[tuple[str, str], list[NormalizedModel]] = {}
    for m in catalog_models:
        by_key.setdefault((m.provider.lower(), m.catalog_id), []).append(m)

    # Local id → file paths
    entries: list[DiffEntry] = []
    seen_ids: set[str] = set()
    for (provider, model), models in by_key.items():
        # Pick the primary source for the id (prefer catwalk if present,
        # else llm_db) so the id is stable across sources.
        primary = next((m for m in models if m.source == "catwalk"), models[0])
        mid = module_id_for_provider_model(provider, model)
        seen_ids.add(mid)
        merged = _merge_models(models)
        if merged is None:
            continue
        module_dir = profiles_dir / mid
        existing_profile = _read_yaml(module_dir / "profile.yaml")
        existing_manifest = _read_manifest(module_dir / "manifest.json")
        action, changes = _diff_entry(merged, existing_profile, existing_manifest)
        # If action is update and changes are empty, that's a no-op — show
        # as skip with an explicit reason.
        if action == "update" and not changes:
            action = "skip"
            changes = ["no changes"]
        entries.append(
            DiffEntry(
                module_id=mid,
                source=merged["source"],
                provider=provider,
                provider_name=primary.provider_name,
                catalog_id=model,
                name=merged.get("name") or f"{model} ({provider})",
                action=action,
                reason=changes[0] if changes else "",
                changes=changes,
            )
        )

    # Stale: local dirs that don't appear in the catalog
    if profiles_dir.is_dir():
        for child in profiles_dir.iterdir():
            if not child.is_dir():
                continue
            mid = child.name
            if mid in seen_ids:
                continue
            # Don't flag dirs that don't look like our id pattern
            if not mid.startswith("llm-"):
                continue
            entries.append(
                DiffEntry(
                    module_id=mid,
                    source="",
                    provider="",
                    provider_name="",
                    catalog_id="",
                    name=mid,
                    action="stale",
                    reason="local dir present, no matching catalog entry",
                    changes=[],
                )
            )

    by_action: dict[str, int] = {}
    for e in entries:
        by_action[e.action] = by_action.get(e.action, 0) + 1

    return ImportReport(
        entries=entries,
        by_action=by_action,
        dry_run=True,
        created_at=datetime.now(UTC).isoformat(),
    )


# ─── Apply ───────────────────────────────────────────────────────────


def _write_module(
    profiles_dir: Path,
    mid: str,
    merged: dict[str, Any],
    *,
    update: bool,
) -> None:
    module_dir = profiles_dir / mid
    module_dir.mkdir(parents=True, exist_ok=True)

    # manifest.json — metadata for the module system
    catalog_source = merged["source"]
    catalog_id = merged["catalog_id"]
    if update and (module_dir / "manifest.json").exists():
        manifest = _read_manifest(module_dir / "manifest.json") or {}
    else:
        manifest = {
            "schema_version": "2.0.0",
            "module_id": mid,
        }
    manifest.update(
        {
            "name": {"en": display_name(catalog_source, merged["provider"], catalog_id, merged.get("name"))},
            "version": "1.0.0",
            "type": "llm-profile",
            "category": "llm-profiles",
            "author": {"name": f"Danwa Catalog Sync ({catalog_source})"},
            "license": "CC-BY-4.0",
            "tags": merged.get("catalog_tags", []),
            "language": "en",
            "profile_file": "profile.yaml",
            "profile_format": "yaml",
            "files": [],
            "catalog_source": catalog_source,
            "catalog_id": catalog_id,
            "catalog_last_synced_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
    )
    if "created_at" not in manifest:
        manifest["created_at"] = datetime.now(UTC).isoformat()
    (module_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # profile.yaml — what BlueprintLLMProfile sees
    profile: dict[str, Any] = {
        "id": mid.replace("llm-", ""),  # short hash without prefix
        "name": manifest["name"]["en"],
        "profile_type": "text",
        "provider": merged["provider"],
        "model": catalog_id,
        "api_key_env": merged.get("api_key_env") or "OPENROUTER_API_KEY",
        "temperature": 0.7,
        "timeout": 600,
        "protocol": "litellm",
    }
    if merged.get("api_base"):
        profile["api_base"] = merged["api_base"]
    elif merged.get("api_endpoint_template"):
        profile["api_base"] = merged["api_endpoint_template"]
    if merged.get("context_window") is not None:
        profile["context_window"] = int(merged["context_window"])
    if merged.get("max_tokens") is not None:
        profile["max_tokens"] = int(merged["max_tokens"])
    if merged.get("cost_per_1m_input") is not None:
        profile["cost_per_1k_input"] = round(float(merged["cost_per_1m_input"]) / 1000.0, 6)
    if merged.get("cost_per_1m_output") is not None:
        profile["cost_per_1k_output"] = round(float(merged["cost_per_1m_output"]) / 1000.0, 6)
    if merged.get("cost_per_1m_cached_input") is not None:
        profile["cost_per_1m_cached_input"] = round(float(merged["cost_per_1m_cached_input"]) / 1000.0, 6)
    if merged.get("cost_per_1m_cached_output") is not None:
        profile["cost_per_1m_cached_output"] = round(float(merged["cost_per_1m_cached_output"]) / 1000.0, 6)
    if merged.get("can_reason"):
        profile["can_reason"] = True
    if merged.get("reasoning_levels"):
        profile["reasoning_levels"] = list(merged["reasoning_levels"])
    if merged.get("default_reasoning_effort"):
        profile["default_reasoning_effort"] = merged["default_reasoning_effort"]
    if merged.get("capabilities"):
        profile["capabilities"] = merged["capabilities"]
    if merged.get("modalities"):
        profile["modalities"] = merged["modalities"]
    if merged.get("lifecycle_status"):
        profile["lifecycle_status"] = merged["lifecycle_status"]
    if merged.get("knowledge_cutoff"):
        profile["knowledge_cutoff"] = merged["knowledge_cutoff"]
    if merged.get("release_date"):
        profile["release_date"] = merged["release_date"]
    if merged.get("last_updated"):
        profile["last_updated"] = merged["last_updated"]
    if merged.get("family"):
        profile["family"] = merged["family"]
    if merged.get("aliases"):
        profile["aliases"] = list(merged["aliases"])
    if merged.get("catalog_tags"):
        profile["catalog_tags"] = list(merged["catalog_tags"])
    if merged.get("api_endpoint_template"):
        profile["api_endpoint_template"] = merged["api_endpoint_template"]
    if merged.get("default_large_model_id"):
        profile["default_large_model_id"] = merged["default_large_model_id"]
    if merged.get("default_small_model_id"):
        profile["default_small_model_id"] = merged["default_small_model_id"]

    (module_dir / "profile.yaml").write_text(
        yaml.safe_dump(profile, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def apply_diff(
    diff: ImportReport,
    catalog_models: list[NormalizedModel],
    settings: Settings,
) -> ImportReport:
    """Apply a dry-run diff.  Returns a new ImportReport with the result."""
    if not diff.dry_run:
        raise ValueError("apply_diff expects a dry_run diff")
    profiles_dir = _llm_profiles_dir(settings)
    profiles_dir.mkdir(parents=True, exist_ok=True)

    # Same bucketing as build_diff()
    by_key: dict[tuple[str, str], NormalizedModel] = {}
    for m in catalog_models:
        key = (m.provider.lower(), m.catalog_id)
        # Prefer catwalk
        if key in by_key and by_key[key].source == "catwalk":
            continue
        by_key[key] = m

    by_action: dict[str, int] = {"create": 0, "update": 0, "stale": 0, "skip": 0}
    for entry in diff.entries:
        if entry.action == "create":
            m = by_key.get((entry.provider, entry.catalog_id))
            if m is None:
                entry.action = "skip"
                entry.reason = "model not in current catalog pass"
                continue
            merged = _merge_models([m, *[
                x for x in catalog_models
                if (x.provider.lower(), x.catalog_id) == (entry.provider, entry.catalog_id) and x.source != m.source
            ]])
            if merged is None:
                entry.action = "skip"
                entry.reason = "merge failed"
                continue
            _write_module(profiles_dir, entry.module_id, merged, update=False)
            by_action["create"] += 1
        elif entry.action == "update":
            m = by_key.get((entry.provider, entry.catalog_id))
            if m is None:
                entry.action = "skip"
                entry.reason = "model not in current catalog pass"
                continue
            merged = _merge_models([m, *[
                x for x in catalog_models
                if (x.provider.lower(), x.catalog_id) == (entry.provider, entry.catalog_id) and x.source != m.source
            ]])
            if merged is None:
                entry.action = "skip"
                entry.reason = "merge failed"
                continue
            _write_module(profiles_dir, entry.module_id, merged, update=True)
            by_action["update"] += 1
        elif entry.action == "stale":
            # Stale: do not delete automatically.  Mark with a flag file
            # the cleanup job can pick up later.
            flag = profiles_dir / entry.module_id / ".stale"
            flag.write_text(
                json.dumps({"flagged_at": datetime.now(UTC).isoformat()}, indent=2),
                encoding="utf-8",
            )
            by_action["stale"] += 1
        else:
            by_action["skip"] += 1

    return ImportReport(
        entries=diff.entries,
        by_action=by_action,
        dry_run=False,
        created_at=diff.created_at,
        finished_at=datetime.now(UTC).isoformat(),
    )


# ─── High-level orchestrator ────────────────────────────────────────


def run_import(
    settings: Settings,
    *,
    dry_run: bool = True,
    sources: list[str] | None = None,
) -> ImportReport:
    """Top-level helper: fetch + normalize + diff + (optionally) apply."""
    from backend.llm_catalog.fetcher import fetch_all
    from backend.llm_catalog.normalize import load_all_normalized

    fetch_results = fetch_all(settings)
    failed = [r for r in fetch_results if r.error]
    if failed and not any(not r.error for r in fetch_results):
        # All fetches failed — return an empty report
        return ImportReport(
            entries=[],
            by_action={},
            dry_run=dry_run,
            created_at=datetime.now(UTC).isoformat(),
        )

    cache_dir = resolve_cache_root(settings)
    chosen_specs: list[SourceSpec]
    if sources:
        from backend.llm_catalog.sources import get_source

        chosen_specs = [get_source(n, settings) for n in sources]
    else:
        chosen_specs = list_sources(settings)

    by_source = load_all_normalized(chosen_specs, cache_dir)
    all_models: list[NormalizedModel] = []
    for models in by_source.values():
        all_models.extend(models)

    diff = build_diff(all_models, settings)
    diff.dry_run = dry_run
    if dry_run:
        diff.finished_at = datetime.now(UTC).isoformat()
        return diff
    return apply_diff(diff, all_models, settings)
