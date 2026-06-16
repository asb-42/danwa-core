"""Catalog source registry.

Each entry is a single public Git repository that hosts a JSON
catalog of LLM providers + models.  The fetcher uses this to
``git clone`` / ``git pull`` the repo and the normalizer uses the
``path`` to know where to find the per-provider JSON files.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from backend.core.config import Settings


@dataclasses.dataclass(frozen=True)
class SourceSpec:
    """Static description of a catalog source."""

    name: str
    repo_url: str
    branch: str
    path: str  # path inside the repo where per-provider JSON files live
    description: str = ""

    @property
    def cache_dir_name(self) -> str:
        return self.name


# Hard-coded registry.  URLs default to settings when available so an
# operator can pin a fork via DANWA_CATALOG_CATWALK_REPO without
# changing code.
def _build_sources(settings: Settings) -> dict[str, SourceSpec]:
    return {
        "catwalk": SourceSpec(
            name="catwalk",
            repo_url=settings.catalog_catwalk_repo,
            branch=settings.catalog_catwalk_branch,
            path=settings.catalog_catwalk_path,
            description="charmbracelet/catwalk — collection of LLM providers and models",
        ),
        "llm_db": SourceSpec(
            name="llm_db",
            repo_url=settings.catalog_llmdb_repo,
            branch=settings.catalog_llmdb_branch,
            path=settings.catalog_llmdb_path,
            description="agentjido/llm_db — model metadata catalog with fast, capability-aware lookups",
        ),
    }


def get_source(name: str, settings: Settings | None = None) -> SourceSpec:
    """Return the spec for a single source name (case-insensitive)."""
    settings = settings or Settings()
    sources = _build_sources(settings)
    key = name.lower()
    if key not in sources:
        raise KeyError(
            f"Unknown catalog source {name!r}. "
            f"Known: {sorted(sources.keys())}"
        )
    return sources[key]


def list_sources(settings: Settings | None = None) -> list[SourceSpec]:
    """Return all configured source specs in stable order."""
    settings = settings or Settings()
    return list(_build_sources(settings).values())


def resolve_cache_root(settings: Settings) -> Path:
    """Where the catalog clones live. Created on first use."""
    root = Path(settings.catalog_cache_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root
