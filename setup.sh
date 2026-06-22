#!/usr/bin/env bash
# danwa-core/setup.sh — Thin shim that delegates to the canonical template.
#
# Canonical source of truth: repo-templates/setup.sh
#
# Mirrors the strategy from plans/2026-06-22_repo-setup-orchestration.md
# §3.2 step 2 (Phase 2 — adopted by danwa-core in Phase 2).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/repo-templates/danwa-core/setup.sh"

if [[ ! -f "$TEMPLATE" ]]; then
    echo "ERROR: canonical setup template not found at $TEMPLATE" >&2
    echo "Hint: git pull — repo-templates/setup.sh is the source of truth." >&2
    exit 1
fi

# Forward all env overrides (DANWA_PROJECT_DIR, DANWA_SKIP_NPM_INSTALL, …)
# Set DANWA_PROJECT_DIR to THIS script's dir (= repo root) unless the caller
# already supplied one. The template uses DANWA_PROJECT_DIR as the repo root
# (it falls back to its own SCRIPT_DIR otherwise, which would point at
# repo-templates/ and confuse the path resolution).
export DANWA_PROJECT_DIR="${DANWA_PROJECT_DIR:-$SCRIPT_DIR}"
exec bash "$TEMPLATE" "$@"