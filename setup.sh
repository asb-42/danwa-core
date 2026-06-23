#!/usr/bin/env bash
# danwa-core/setup.sh — Thin shim that delegates to the canonical template.
#
# Canonical source of truth: repo-templates/danwa-core/setup.sh
#
# Mirrors the strategy from plans/2026-06-22_repo-setup-orchestration.md
# §3.2 step 2 (Phase 2 — adopted by danwa-core in Phase 2).
#
# Usage (forwarded to the template):
#     bash setup.sh              # minimal install (~200 MB, no GPU OCR)
#     bash setup.sh --gpu        # + easyocr → torch + nvidia-cu* (~3 GB)
#     bash setup.sh --help       # full help
#     FULL_GPU=1 bash setup.sh   # legacy alias for --gpu

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/repo-templates/danwa-core/setup.sh"

if [[ ! -f "$TEMPLATE" ]]; then
    echo "ERROR: canonical setup template not found at $TEMPLATE" >&2
    echo "Hint: git pull — repo-templates/danwa-core/setup.sh is the source of truth." >&2
    exit 1
fi

# Quick local validation of the flags we forward — keeps the error
# message close to the user. Full validation lives in the template.
for arg in "$@"; do
    case "$arg" in
        --gpu|--help|-h) ;;
        --*) echo "ERROR: unknown flag: $arg" >&2
             echo "Run 'bash setup.sh --help' for supported flags." >&2
             exit 2 ;;
    esac
done

# Forward all env overrides (DANWA_PROJECT_DIR, DANWA_SKIP_NPM_INSTALL, FULL_GPU, …)
# Set DANWA_PROJECT_DIR to THIS script's dir (= repo root) unless the caller
# already supplied one. The template uses DANWA_PROJECT_DIR as the repo root
# (it falls back to its own SCRIPT_DIR otherwise, which would point at
# repo-templates/ and confuse the path resolution).
export DANWA_PROJECT_DIR="${DANWA_PROJECT_DIR:-$SCRIPT_DIR}"
exec bash "$TEMPLATE" "$@"