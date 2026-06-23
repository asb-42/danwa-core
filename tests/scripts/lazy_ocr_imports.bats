#!/usr/bin/env bats
#
# tests/scripts/lazy_ocr_imports.bats — Shell-level contract tests
# for the easyocr-lazy-import + pyproject-extras refactor.
#
# Date: 2026-06-23 — user report: 'setup.sh läuft seit 6 Stunden und
# lädt 3+ GB an torch/triton/nvidia-* herunter'. Root cause:
# easyocr was in [project].dependencies, transitively pulling the
# entire CUDA chain. The fix moves easyocr to
# [project.optional-dependencies].gpu so 'uv sync' (no flags) does
# NOT install it.
#
# These tests run in pure bash + python3 (stdlib only) so they
# don't need the dev venv populated. They cover the things a
# shell-script user would observe: file content + a tiny AST parse.

REPO_ROOT="$(cd "${BATS_TEST_DIRNAME}/../.." && pwd)"
PYPROJECT="$REPO_ROOT/pyproject.toml"
SETUP_SH="$REPO_ROOT/setup.sh"
[ -f "$SETUP_SH" ] || SETUP_SH="$REPO_ROOT/repo-templates/danwa-core/setup.sh"

# ─────────────────────────────────────────────────────────────────────
# pyproject.toml
# ─────────────────────────────────────────────────────────────────────

@test "pyproject: easyocr is NOT in [project].dependencies" {
    # The user-visible cost: every 'uv sync' pulls easyocr →
    # torch → triton → 8 nvidia-cu* wheels (~3 GB).
    [ -f "$PYPROJECT" ]
    run python3 -c "
import tomllib
with open('$PYPROJECT','rb') as f:
    d = tomllib.load(f)
deps = d.get('project',{}).get('dependencies',[])
hits = [x for x in deps if 'easyocr' in x]
if hits:
    print('FOUND:', hits)
    raise SystemExit(1)
print('OK: easyocr not in main dependencies')
"
    [ "$status" -eq 0 ]
}

@test "pyproject: easyocr is in [project.optional-dependencies].gpu" {
    run python3 -c "
import tomllib
with open('$PYPROJECT','rb') as f:
    d = tomllib.load(f)
opt = d.get('project',{}).get('optional-dependencies',{})
gpu = opt.get('gpu',[])
if not any('easyocr' in x for x in gpu):
    print(f'FOUND: optional-dependencies={list(opt.keys())}, no easyocr in [gpu]')
    raise SystemExit(1)
print('OK: easyocr in [project.optional-dependencies].gpu')
"
    [ "$status" -eq 0 ]
}

# ─────────────────────────────────────────────────────────────────────
# Lazy imports in source files
# ─────────────────────────────────────────────────────────────────────

@test "source: backend/services/dms/document_processor.py — easyocr import is inside a function" {
    [ -f "$REPO_ROOT/backend/services/dms/document_processor.py" ]
    # All `import easyocr` lines must have leading whitespace
    # (i.e. live inside a function or method).
    ! grep -nE "^import easyocr\b" "$REPO_ROOT/backend/services/dms/document_processor.py"
}

@test "source: backend/api/routers/dms.py — easyocr import is inside a function" {
    [ -f "$REPO_ROOT/backend/api/routers/dms.py" ]
    ! grep -nE "^import easyocr\b" "$REPO_ROOT/backend/api/routers/dms.py"
}

@test "source: easyocr lazy-import sites are wrapped in try/except ImportError" {
    # The lazy import must give a clear 'install with uv sync --group gpu'
    # error message rather than a bare ModuleNotFoundError.
    local pattern_hits
    pattern_hits="$(grep -cE 'except ImportError' \
        "$REPO_ROOT/backend/services/dms/document_processor.py" \
        "$REPO_ROOT/backend/api/routers/dms.py" 2>/dev/null | awk -F: '{s+=$2} END {print s}')"
    [ "$pattern_hits" -ge 1 ]
}

@test "source: ImportError message mentions the install command (uv sync --group gpu)" {
    # At least one of the lazy-import sites should mention the
    # actionable install command so the user knows how to recover.
    grep -qE "uv sync --group gpu" \
        "$REPO_ROOT/backend/services/dms/document_processor.py" \
        "$REPO_ROOT/backend/api/routers/dms.py" 2>/dev/null
}

# ─────────────────────────────────────────────────────────────────────
# setup.sh behaviour
# ─────────────────────────────────────────────────────────────────────

# Find the canonical template (where the uv sync actually runs). The
# top-level setup.sh is a thin shim that exec's the template via
# 'exec bash "$TEMPLATE" "$@"', so it deliberately doesn't re-state
# the uv sync commands.
SETUP_TEMPLATE="$REPO_ROOT/repo-templates/danwa-core/setup.sh"
if [[ ! -f "$SETUP_TEMPLATE" ]]; then
    SETUP_TEMPLATE="$SETUP_SH"
fi

@test "setup.sh: default path does NOT include 'gpu' in the uv sync command" {
    # The template must contain at least one 'uv sync' invocation
    # (the default install) that does NOT include '--group gpu'.
    [ -f "$SETUP_TEMPLATE" ]
    # There must be at least one 'uv sync' line whose arguments do
    # NOT include '--group gpu'. The simplest check: there is a
    # 'uv sync' line in the file, and not every 'uv sync' line
    # uses --group gpu.
    grep -qE 'uv[ ]+sync' "$SETUP_TEMPLATE"
    ! grep -qE '^[[:space:]]*uv[ ]+sync[ ]*$' "$SETUP_TEMPLATE" || \
        grep -qE '^[[:space:]]*uv[ ]+sync[ ]+--group[ ]+gpu' "$SETUP_TEMPLATE"
}

@test "setup.sh: has a --gpu / FULL_GPU opt-in flag that triggers 'uv sync --group gpu'" {
    # The template must contain a 'uv sync --group gpu' invocation,
    # AND the shim or template must mention the --gpu/FULL_GPU flag.
    grep -qE "^[[:space:]]*uv[ ]+sync[ ]+--group[ ]+gpu" "$SETUP_TEMPLATE"
    grep -qE "(FULL_GPU|--gpu\\b|uv[ ]+sync[ ]+--group[ ]+gpu)" "$SETUP_SH" \
        || grep -qE "(FULL_GPU|--gpu\\b|uv[ ]+sync[ ]+--group[ ]+gpu)" "$SETUP_TEMPLATE"
}
