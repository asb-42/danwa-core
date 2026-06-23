#!/usr/bin/env bash
# repo-templates/danwa-core/setup.sh
#
# CANONICAL SETUP TEMPLATE for danwa-core.
#
# This file is the single source of truth for the danwa-core setup
# procedure. Mirror it (or symlink to it) into a danwa-core clone as
# `setup.sh` at the repo root.
#
# Usage (from inside the cloned danwa-core repo):
#     bash setup.sh                  # minimal install (~200 MB, no GPU OCR)
#     bash setup.sh --gpu            # + easyocr → torch + nvidia-cu* (~3 GB)
#     bash setup.sh --help           # show flags
#
# Environment overrides:
#   DANWA_PROJECT_DIR=/path/to/project  bash setup.sh [flags...]
#   FULL_GPU=1                          bash setup.sh   # legacy alias for --gpu
#
# What it does:
#   1. Parses flags (--gpu, --help)
#   2. Validates .danwa-config exists
#   3. Checks toolchain (uv, python3)
#   4. Fetches libdanwa.sh into .lib/ if missing (offline-friendly)
#   5. Detects sibling repos (danwa, danwa-studio) in parent dir
#   6. Runs uv sync (with or without --group gpu per --gpu flag)
#
# Idempotent: re-running is safe.
# ───────────────────────────────────────────────────────────────────────
# Background: easyocr was previously in [project].dependencies, which
# transitively pulled torch + triton + 8 nvidia-cu* wheels (~3 GB) on
# every fresh clone. Moving it to [project.optional-dependencies].gpu
# + the --gpu flag below brings the default install under a minute
# and under 200 MB. The corresponding `import easyocr` calls are
# already lazy (inside function bodies), so the backend boots either
# way; the failure surfaces only when OCR is actually exercised.

set -uo pipefail
# NOTE: -u is unsafe because some env vars may be unset on first run.

# ───────────────────────────────────────────────────────────────────────
# Flag parsing (--gpu / --help). FULL_GPU=1 is honoured as a legacy alias.
# ───────────────────────────────────────────────────────────────────────
INSTALL_GPU=0
print_help() {
    cat <<EOF
danwa-core setup.sh — install Python deps for the backend.

Usage:
    bash setup.sh              # minimal install (~200 MB, no GPU OCR)
    bash setup.sh --gpu        # + easyocr → torch + nvidia-cu* (~3 GB)
    bash setup.sh --help       # this message

Environment overrides:
    DANWA_PROJECT_DIR=/path    # run as if invoked from this dir
    FULL_GPU=1                 # legacy alias for --gpu
EOF
}
for arg in "$@"; do
    case "$arg" in
        --gpu)
            INSTALL_GPU=1
            shift
            ;;
        --help|-h)
            print_help
            exit 0
            ;;
        *)
            echo "ERROR: unknown flag: $arg" >&2
            print_help >&2
            exit 2
            ;;
    esac
done
if [[ -n "${FULL_GPU:-}" && "$FULL_GPU" == "1" ]]; then
    INSTALL_GPU=1
fi

# Resolve paths: prefer $DANWA_PROJECT_DIR (for tests + mirror templates),
# otherwise default to the directory where setup.sh lives.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${DANWA_PROJECT_DIR:-$SCRIPT_DIR}"
LIB_DIR="$PROJECT_DIR/.lib"
LOG_DIR="$PROJECT_DIR/logs"
PID_DIR="$PROJECT_DIR/pids"
CONFIG_FILE="$PROJECT_DIR/.danwa-config"

# Find libdanwa.sh (in priority order):
#   1. $DANWA_LIBDANWA_PATH env override (for tests + mirror templates)
#   2. ./.lib/libdanwa.sh (preferred — local install)
#   3. ./scripts/libdanwa.sh (when this repo IS danwa, used as source)
find_libdanwa() {
    if [[ -n "${DANWA_LIBDANWA_PATH:-}" ]] && [[ -f "$DANWA_LIBDANWA_PATH" ]]; then
        echo "$DANWA_LIBDANWA_PATH"
        return 0
    fi
    if [[ -f "$LIB_DIR/libdanwa.sh" ]]; then
        echo "$LIB_DIR/libdanwa.sh"
        return 0
    fi
    if [[ -f "$PROJECT_DIR/scripts/libdanwa.sh" ]]; then
        echo "$PROJECT_DIR/scripts/libdanwa.sh"
        return 0
    fi
    return 1
}

# Source the library (tries find_libdanwa, exits if not found)
LIBDANWA_PATH_RESOLVED="$(find_libdanwa)" || {
    echo "ERROR: libdanwa.sh not found. Looked in:" >&2
    echo "  - \$DANWA_LIBDANWA_PATH env override" >&2
    echo "  - $LIB_DIR/libdanwa.sh" >&2
    echo "  - $PROJECT_DIR/scripts/libdanwa.sh" >&2
    echo "" >&2
    echo "Hint: fetch with 'curl -L <danwa-modules-raw-url>/scripts/libdanwa.sh -o .lib/libdanwa.sh'" >&2
    echo "or copy from the danwa monorepo: cp ../danwa/scripts/libdanwa.sh .lib/libdanwa.sh" >&2
    exit 1
}
# shellcheck disable=SC1090
source "$LIBDANWA_PATH_RESOLVED"

# ───────────────────────────────────────────────────────────────────────
# Step 1: Validate .danwa-config
# ───────────────────────────────────────────────────────────────────────
log_step "1/6: Validating .danwa-config"
if [[ ! -f "$CONFIG_FILE" ]]; then
    log_error ".danwa-config not found at $CONFIG_FILE"
    log_info "Expected KEY=VALUE pairs: REPO_NAME, BACKEND_PORT, etc."
    exit 1
fi
log_ok "Found $CONFIG_FILE"

# ───────────────────────────────────────────────────────────────────────
# Step 2: Toolchain checks
# ───────────────────────────────────────────────────────────────────────
log_step "2/6: Checking toolchain"
require_cmd python3 || exit 1
require_cmd uv || {
    log_info "Install uv with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
}
log_ok "Python 3 + uv present"

# ───────────────────────────────────────────────────────────────────────
# Step 3: Ensure .lib/libdanwa.sh is present
# ───────────────────────────────────────────────────────────────────────
log_step "3/6: Ensuring libdanwa.sh"
ensure_dir "$LIB_DIR"
if [[ ! -f "$LIB_DIR/libdanwa.sh" ]]; then
    cp "$LIBDANWA_PATH_RESOLVED" "$LIB_DIR/libdanwa.sh"
    log_ok "Copied libdanwa.sh into .lib/"
else
    log_ok "libdanwa.sh already in .lib/"
fi

# ───────────────────────────────────────────────────────────────────────
# Step 4: Detect sibling repos in parent directory
# ───────────────────────────────────────────────────────────────────────
log_step "4/6: Detecting sibling repos"
PARENT_DIR="$(cd "$PROJECT_DIR/.." && pwd)"
SIBLINGS_FOUND=0
for sibling_name in danwa danwa-studio; do
    if [[ -d "$PARENT_DIR/$sibling_name" ]]; then
        log_ok "Found sibling: $PARENT_DIR/$sibling_name"
        SIBLINGS_FOUND=$((SIBLINGS_FOUND + 1))
    fi
done
if [[ $SIBLINGS_FOUND -eq 0 ]]; then
    log_warn "No sibling repos found in $PARENT_DIR (expected: danwa, danwa-studio)"
    log_info "Orchestrator mode requires both; standalone mode works without."
else
    log_ok "Found $SIBLINGS_FOUND sibling(s)"
fi

# ───────────────────────────────────────────────────────────────────────
# Step 5: Install Python deps via uv sync
# ───────────────────────────────────────────────────────────────────────
log_step "5/6: Installing Python dependencies (uv sync)"
cd "$PROJECT_DIR"
if [[ -f "pyproject.toml" ]]; then
    if [[ "$INSTALL_GPU" == "1" ]]; then
        log_info "GPU OCR requested (--gpu / FULL_GPU=1): pulling easyocr → torch + nvidia-cu* (~3 GB)."
        log_info "This may take 5–30 min on a fresh clone depending on bandwidth."
        uv sync --group gpu || {
            log_error "uv sync --group gpu failed"
            exit 1
        }
        log_ok "Python dependencies installed (including gpu group: easyocr)"
    else
        log_info "Default install: skipping easyocr → torch + nvidia-cu* (~3 GB)."
        log_info "Pass --gpu (or set FULL_GPU=1) to enable GPU-accelerated OCR."
        log_info "The backend boots either way; easyocr is imported lazily on demand."
        uv sync || {
            log_error "uv sync failed"
            exit 1
        }
        log_ok "Python dependencies installed (minimal: no gpu group)"
    fi
else
    log_warn "No pyproject.toml found — skipping uv sync (expected for the mirror template)"
fi

# ───────────────────────────────────────────────────────────────────────
# Done
# ───────────────────────────────────────────────────────────────────────
ensure_dir "$LOG_DIR"
ensure_dir "$PID_DIR"
log_header "Setup complete!"
log_info "Next steps:"
log_info "  - Run 'bash manage.sh start' to start the backend (and sibling apps if present)"
log_info "  - Run 'bash manage.sh status' to check status"
log_info "  - Run 'bash manage.sh help' for all commands"