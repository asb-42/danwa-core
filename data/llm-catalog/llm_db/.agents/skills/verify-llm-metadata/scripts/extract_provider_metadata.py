#!/usr/bin/env python3
"""Extract one provider's llm_db metadata stack for documentation audits."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - depends on host Python.
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None


COMPACT_MODEL_FIELDS = (
    "id",
    "model",
    "provider",
    "provider_model_id",
    "name",
    "family",
    "doc_url",
    "release_date",
    "last_updated",
    "knowledge",
    "base_url",
    "limits",
    "cost",
    "pricing",
    "modalities",
    "capabilities",
    "deprecated",
    "retired",
    "lifecycle",
    "execution",
    "catalog_only",
    "aliases",
    "extra",
)

RAW_REMOTE_MODEL_FIELDS = (
    "id",
    "type",
    "display_name",
    "created_at",
    "max_input_tokens",
    "max_tokens",
    "capabilities",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract one provider from snapshot, generated files, local TOML, and remote cache."
    )
    parser.add_argument("provider", help="Provider ID, for example openai or anthropic")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root containing priv/llm_db (default: current directory)",
    )
    parser.add_argument(
        "--snapshot",
        default=None,
        help="Optional snapshot JSON path (default: <repo-root>/priv/llm_db/snapshot.json)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Write JSON output to this path instead of stdout",
    )
    parser.add_argument(
        "--include-empty",
        action="store_true",
        help="Keep keys with null, empty list, or empty map values in compact model output",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_toml(path: Path) -> dict:
    if tomllib is None:
        raise RuntimeError("TOML parsing requires Python 3.11+ or the tomli package")

    with path.open("rb") as file:
        return tomllib.load(file)


def compact_model(model: dict, include_empty: bool) -> dict:
    compact = {field: model.get(field) for field in COMPACT_MODEL_FIELDS if field in model}

    if include_empty:
        return compact

    return {
        key: value
        for key, value in compact.items()
        if value is not None and value != [] and value != {}
    }


def compact_raw_remote_model(model: dict, include_empty: bool) -> dict:
    compact = {field: model.get(field) for field in RAW_REMOTE_MODEL_FIELDS if field in model}

    if include_empty:
        return compact

    return {
        key: value
        for key, value in compact.items()
        if value is not None and value != [] and value != {}
    }


def snapshot_provider(snapshot_path: Path, provider: str, include_empty: bool) -> dict | None:
    if not snapshot_path.exists():
        return None

    snapshot = load_json(snapshot_path)
    providers = snapshot.get("providers", {})
    provider_data = providers.get(provider)

    if provider_data is None:
        return None

    models = provider_data.get("models", {})

    if isinstance(models, list):
        compact_models = {
            model.get("id", f"model_{index}"): compact_model(model, include_empty)
            for index, model in enumerate(models)
            if isinstance(model, dict)
        }
    else:
        compact_models = {
            model_id: compact_model(model, include_empty)
            for model_id, model in sorted(models.items())
            if isinstance(model, dict)
        }

    provider_fields = {
        key: value
        for key, value in provider_data.items()
        if key != "models" and (include_empty or value not in (None, [], {}))
    }

    return {
        "path": str(snapshot_path),
        "provider": provider_fields,
        "model_count": len(compact_models),
        "models": compact_models,
    }


def local_provider(repo_root: Path, provider: str) -> dict | None:
    local_dir = repo_root / "priv" / "llm_db" / "local" / provider

    if not local_dir.exists():
        return None

    provider_path = local_dir / "provider.toml"

    if provider_path.exists():
        try:
            provider_data = load_toml(provider_path)
        except Exception as error:  # noqa: BLE001 - audit helper should report bad files.
            provider_data = {"_parse_error": str(error)}
    else:
        provider_data = None

    models = {}

    for path in sorted(local_dir.glob("*.toml")):
        if path.name == "provider.toml":
            continue

        try:
            data = load_toml(path)
        except Exception as error:  # noqa: BLE001 - audit helper should report bad files.
            data = {"_parse_error": str(error)}

        model_id = data.get("id") or path.stem
        models[model_id] = {"path": str(path), "data": data}

    return {
        "path": str(local_dir),
        "provider_toml": str(provider_path) if provider_path.exists() else None,
        "provider": provider_data,
        "model_count": len(models),
        "models": models,
    }


def remote_cache(repo_root: Path, provider: str, include_empty: bool) -> list[dict]:
    remote_dir = repo_root / "priv" / "llm_db" / "remote"

    if not remote_dir.exists():
        return []

    caches = []

    for path in sorted(remote_dir.glob(f"{provider}-*.json")):
        if path.name.endswith(".manifest.json"):
            continue

        manifest_path = path.with_name(f"{path.stem}.manifest.json")
        manifest = load_json(manifest_path) if manifest_path.exists() else None
        data = load_json(path)
        raw_models = data.get("data", data if isinstance(data, list) else [])

        if isinstance(raw_models, list):
            models = {
                model.get("id", f"model_{index}"): compact_raw_remote_model(model, include_empty)
                for index, model in enumerate(raw_models)
                if isinstance(model, dict)
            }
        else:
            models = {}

        caches.append(
            {
                "path": str(path),
                "manifest_path": str(manifest_path) if manifest_path.exists() else None,
                "manifest": manifest,
                "model_count": len(models),
                "models": models,
            }
        )

    return caches


def compact_provider_data(provider_data: dict, include_empty: bool) -> dict:
    return {
        key: value
        for key, value in provider_data.items()
        if key != "models" and (include_empty or value not in (None, [], {}))
    }


def provider_registry(repo_root: Path, provider: str, include_empty: bool) -> dict | None:
    registry_path = repo_root / "priv" / "llm_db" / "providers" / f"{provider}.json"

    if not registry_path.exists():
        return None

    data = load_json(registry_path)
    models = data.get("models", {})

    if isinstance(models, dict):
        compact_models = {
            model_id: compact_model(model, include_empty)
            for model_id, model in sorted(models.items())
            if isinstance(model, dict)
        }
    else:
        compact_models = {}

    return {
        "path": str(registry_path),
        "provider": compact_provider_data(data, include_empty),
        "model_count": len(compact_models),
        "models": compact_models,
    }


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    snapshot_path = (
        Path(args.snapshot).resolve()
        if args.snapshot
        else repo_root / "priv" / "llm_db" / "snapshot.json"
    )

    result = {
        "provider_id": args.provider,
        "repo_root": str(repo_root),
        "toml_parse_available": tomllib is not None,
        "snapshot": snapshot_provider(snapshot_path, args.provider, args.include_empty),
        "local": local_provider(repo_root, args.provider),
        "remote_cache": remote_cache(repo_root, args.provider, args.include_empty),
        "provider_registry": provider_registry(repo_root, args.provider, args.include_empty),
    }

    if (
        result["snapshot"] is None
        and result["local"] is None
        and result["provider_registry"] is None
        and result["remote_cache"] == []
    ):
        print(f"Provider not found: {args.provider}", file=sys.stderr)
        return 2

    output = json.dumps(result, indent=2, sort_keys=True)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
