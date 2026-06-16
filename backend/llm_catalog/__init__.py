"""LLM Catalog integration — public API surface.

The catalog lets Danwa pull structured model metadata (cost, context
window, capabilities, reasoning) from public GitHub-hosted databases
(currently ``charmbracelet/catwalk`` and ``agentjido/llm_db``) and
materialize them as ``llm-profiles`` modules.

Sub-modules:
- sources: registry of known catalogs (URL, branch, JSON path)
- fetcher: ``git clone`` / ``git pull`` into a local cache
- normalize: per-source JSON → uniform ``NormalizedModel`` dicts
- import_engine: diff / apply upsert into the local modules dir
- id_strategy: deterministic module-id from (provider, model)

Public entry points:
- :func:`fetch_source` — ensure a source is checked out locally
- :func:`load_normalized` — load + parse one or all sources
- :func:`diff_local_vs_catalog` — what would change?
- :func:`apply_import` — perform the upsert

All operations are subprocess-bounded (git clone with timeout) and
read-only on the catalog side; writes only happen in the local
``danwa-modules/llm-profiles/`` tree.
"""
