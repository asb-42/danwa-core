#!/usr/bin/env bash
# danwa-core/manage.sh — Thin shim that delegates to the canonical template.
#
# Canonical source of truth: repo-templates/manage.sh
# (This shim exists so `bash manage.sh <cmd>` at the repo root keeps
#  working exactly as before, while the actual logic — and tests —
#  live in the template.)
#
# Mirrors the strategy from plans/2026-06-22_repo-setup-orchestration.md
# §3.2 step 8 (Phase 8 — adopted by danwa-core in Phase 3).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/repo-templates/danwa-core/manage.sh"

if [[ ! -f "$TEMPLATE" ]]; then
    echo "ERROR: canonical manage template not found at $TEMPLATE" >&2
    echo "Hint: git pull — repo-templates/manage.sh is the source of truth." >&2
    exit 1
fi

# Forward all env overrides (DANWA_PROJECT_DIR, DANWA_USE_MOCK, BACKEND_PORT, …)
# and all CLI arguments unchanged. Set DANWA_PROJECT_DIR to THIS script's
# dir (= repo root) unless the caller already supplied one.
export DANWA_PROJECT_DIR="${DANWA_PROJECT_DIR:-$SCRIPT_DIR}"
exec bash "$TEMPLATE" "$@"