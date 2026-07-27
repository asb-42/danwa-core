#!/usr/bin/env python3
"""Run or print one verify-llm-metadata Codex invocation per provider."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PROVIDER_SETS = SKILL_DIR / "references" / "provider-sets.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Orchestrate one $verify-llm-metadata invocation per provider."
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Path to the llm_db repository root (default: current directory)",
    )
    parser.add_argument(
        "--provider-sets",
        default=str(DEFAULT_PROVIDER_SETS),
        help="Provider set JSON path",
    )
    parser.add_argument(
        "--provider-set",
        default="popular_core",
        help="Provider set name from provider-sets.json (default: popular_core)",
    )
    parser.add_argument(
        "--providers",
        nargs="+",
        help="Explicit provider IDs. Overrides --provider-set.",
    )
    parser.add_argument(
        "--list-sets",
        action="store_true",
        help="List configured provider sets and exit.",
    )
    parser.add_argument(
        "--mode",
        choices=("pr", "audit-only"),
        default="pr",
        help="Whether each provider invocation should prepare a PR or only report findings.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Run codex exec for each provider. Default prints the planned commands.",
    )
    parser.add_argument(
        "--base",
        default="main",
        help="Base branch/ref for per-provider worktrees when --execute is used.",
    )
    parser.add_argument(
        "--branch-prefix",
        default="verify-llm-metadata",
        help="Branch prefix for per-provider worktrees.",
    )
    parser.add_argument(
        "--worktree-root",
        default="tmp/llm-metadata-provider-audits",
        help="Directory for per-provider git worktrees and final reports.",
    )
    parser.add_argument(
        "--reuse-worktrees",
        action="store_true",
        help="Reuse existing provider worktree directories instead of failing.",
    )
    parser.add_argument(
        "--start-at",
        help="Skip providers before this provider ID.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of providers to process.",
    )
    parser.add_argument(
        "--codex-bin",
        default="codex",
        help="Codex executable to run when --execute is used.",
    )
    parser.add_argument(
        "--model",
        help="Optional Codex model name.",
    )
    parser.add_argument(
        "--profile",
        help="Optional Codex config profile.",
    )
    parser.add_argument(
        "--no-search",
        action="store_true",
        help="Do not pass --search to codex.",
    )
    parser.add_argument(
        "--sandbox",
        default="danger-full-access",
        help="Codex sandbox mode for provider invocations.",
    )
    parser.add_argument(
        "--approval",
        default="never",
        help="Codex approval policy for provider invocations.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue to the next provider if one invocation fails.",
    )
    return parser.parse_args()


def load_provider_sets(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"Provider set file not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict) or not isinstance(data.get("sets"), dict):
        raise SystemExit(f"Provider set file must contain a top-level 'sets' object: {path}")

    return data


def repo_providers(repo_root: Path) -> set[str]:
    providers: set[str] = set()

    local_root = repo_root / "priv" / "llm_db" / "local"
    if local_root.exists():
        providers.update(path.name for path in local_root.iterdir() if path.is_dir())

    provider_root = repo_root / "priv" / "llm_db" / "providers"
    if provider_root.exists():
        providers.update(path.stem for path in provider_root.glob("*.json"))

    snapshot_path = repo_root / "priv" / "llm_db" / "snapshot.json"
    if snapshot_path.exists():
        with snapshot_path.open("r", encoding="utf-8") as file:
            snapshot = json.load(file)
        providers.update(snapshot.get("providers", {}).keys())

    return providers


def selected_providers(args: argparse.Namespace, provider_sets: dict) -> list[str]:
    if args.providers:
        providers = args.providers
    else:
        sets = provider_sets.get("sets", {})
        if args.provider_set not in sets:
            available = ", ".join(sorted(sets))
            raise SystemExit(
                f"Unknown provider set: {args.provider_set}. Available sets: {available}"
            )
        providers = sets[args.provider_set].get("providers", [])

    duplicate_providers = sorted(
        {provider for provider in providers if providers.count(provider) > 1}
    )

    if duplicate_providers:
        raise SystemExit("Duplicate providers selected: " + ", ".join(duplicate_providers))

    if args.start_at:
        try:
            start_index = providers.index(args.start_at)
        except ValueError as error:
            raise SystemExit(f"--start-at provider not in selection: {args.start_at}") from error
        providers = providers[start_index:]

    if args.limit is not None:
        providers = providers[: args.limit]

    if not providers:
        raise SystemExit("No providers selected.")

    return providers


def prompt_for(provider: str, mode: str) -> str:
    common = f"""Use $verify-llm-metadata to verify provider `{provider}` only.

Requirements:
- Treat this as one isolated provider verification run.
- If $verify-llm-metadata is not available in this worktree, read and follow `{SKILL_DIR / "SKILL.md"}`.
- Use bundled skill resources from `{SKILL_DIR}` when this worktree does not contain `.agents/skills/verify-llm-metadata`.
- Do not edit, stage, commit, or copy the external skill directory into the provider PR.
- Use public provider documentation, public model API data, and this repo's metadata stack.
- Choose the recommended capture layer for every finding before editing.
- Do not hand-edit generated provider JSON or snapshot artifacts.
- Stop after this provider; do not continue to another provider.
"""

    if mode == "audit-only":
        return (
            common
            + """
Audit-only mode:
- Do not edit files.
- Do not commit.
- Do not open a PR.
- Produce the provider report with sources, findings, capture layers, and unverified fields.
"""
        )

    return (
        common
        + """
PR mode:
- Create or use a provider-specific branch.
- Implement source-layer fixes only where public evidence supports the change.
- Rebuild generated metadata artifacts after source-layer edits.
- Run appropriate tests.
- Commit with a Conventional Commit message.
- Open a provider-specific PR and include evidence links, capture layers, tests, and residual uncertainty.
"""
    )


def shell_join(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def codex_command(
    args: argparse.Namespace,
    provider: str,
    worktree_path: Path,
    final_message_path: Path,
) -> list[str]:
    command = [args.codex_bin]

    if not args.no_search:
        command.append("--search")

    if args.model:
        command.extend(["--model", args.model])

    if args.profile:
        command.extend(["--profile", args.profile])

    if args.approval:
        command.extend(["--ask-for-approval", args.approval])

    command.extend(
        [
            "exec",
            "--cd",
            str(worktree_path),
            "--sandbox",
            args.sandbox,
            "--output-last-message",
            str(final_message_path),
            prompt_for(provider, args.mode),
        ]
    )

    return command


def run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=str(cwd), check=True)


def ensure_worktree(
    repo_root: Path,
    worktree_root: Path,
    provider: str,
    args: argparse.Namespace,
) -> Path:
    worktree_path = worktree_root / provider

    if worktree_path.exists():
        if args.reuse_worktrees:
            return worktree_path
        raise SystemExit(
            f"Worktree already exists for {provider}: {worktree_path}. "
            "Use --reuse-worktrees to reuse it."
        )

    branch = f"{args.branch_prefix}/{provider}"
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "worktree", "add", "-b", branch, str(worktree_path), args.base], repo_root)
    return worktree_path


def list_sets(provider_sets: dict) -> None:
    for name, data in sorted(provider_sets.get("sets", {}).items()):
        providers = data.get("providers", [])
        print(f"{name}: {len(providers)} providers")
        print(f"  {data.get('description', '')}")
        print(f"  {', '.join(providers)}")


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    provider_sets = load_provider_sets(Path(args.provider_sets).resolve())

    if args.list_sets:
        list_sets(provider_sets)
        return 0

    providers = selected_providers(args, provider_sets)
    known_providers = repo_providers(repo_root)
    missing = [provider for provider in providers if provider not in known_providers]

    if missing:
        print(
            "Warning: selected providers are not present in local metadata: "
            + ", ".join(missing),
            file=sys.stderr,
        )

    worktree_root = (repo_root / args.worktree_root).resolve()
    report_root = worktree_root / "_reports"

    print(f"Provider set: {args.provider_set if not args.providers else 'explicit'}")
    print(f"Mode: {args.mode}")
    print(f"Providers: {', '.join(providers)}")

    failed = []

    for provider in providers:
        if args.execute:
            worktree_path = ensure_worktree(repo_root, worktree_root, provider, args)
        else:
            worktree_path = worktree_root / provider

        final_message_path = report_root / f"{provider}.final.md"
        command = codex_command(args, provider, worktree_path, final_message_path)

        if not args.execute:
            print()
            print(f"# {provider}")
            print(shell_join(command))
            continue

        report_root.mkdir(parents=True, exist_ok=True)
        print(f"\n==> {provider}")
        try:
            run(command, repo_root)
        except subprocess.CalledProcessError:
            failed.append(provider)
            if not args.continue_on_error:
                raise

    if failed:
        print("Failed providers: " + ", ".join(failed), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
