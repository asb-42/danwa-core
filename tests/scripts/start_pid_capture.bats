#!/usr/bin/env bats
#
# tests/scripts/start_pid_capture.bats — Regression test for the
# `$! is empty` bug fixed in 2026-06-22.
#
# The start_backend() and sibling lifecycle functions in the danwa-core
# orchestrator template used the pattern
#     (cd DIR && nohup CMD &)
# which spawns the job inside a subshell, so `$!` outside the subshell
# was empty. Fixed in 4 sites: start_backend, start_backend_no_watcher,
# start_frontend_user, start_studio.
#
# These tests verify the fix end-to-end.

setup() {
    TEST_TMP="$(mktemp -d /tmp/danwa-core-start-pid-XXXXXX)"
    export TEST_TMP
    PROJECT_DIR="$TEST_TMP/danwa-core"
    mkdir -p "$PROJECT_DIR"
    export PROJECT_DIR
    export DANWA_PROJECT_DIR="$PROJECT_DIR"

    MANAGE_SCRIPT="$(cd "${BATS_TEST_DIRNAME}/../.." && pwd)/repo-templates/danwa-core/manage.sh"
    LIBDANWA_PATH="$(cd "${BATS_TEST_DIRNAME}/../.." && pwd)/scripts/libdanwa.sh"

    cat > "$PROJECT_DIR/.danwa-config" <<EOF
REPO_NAME="danwa-core"
BACKEND_PORT=18000
FRONTEND_PORT=15173
STUDIO_PORT=15174
SIBLINGS=()
EOF

    mkdir -p "$PROJECT_DIR/.lib"
    cp "$LIBDANWA_PATH" "$PROJECT_DIR/.lib/libdanwa.sh"

    export DANWA_USE_MOCK=1
    export BACKEND_PORT=18000
    export FRONTEND_PORT=15173
    export STUDIO_PORT=15174
}

teardown() {
    bash "$MANAGE_SCRIPT" stop >/dev/null 2>&1 || true
    rm -rf "$TEST_TMP"
    unset DANWA_PROJECT_DIR DANWA_USE_MOCK BACKEND_PORT FRONTEND_PORT STUDIO_PORT
}

@test "start (mock): writes a non-empty backend.pid" {
    run bash "$MANAGE_SCRIPT" start be
    [ "$status" -eq 0 ]
    [ -f "$PROJECT_DIR/pids/backend.pid" ]
    local pid
    pid="$(cat "$PROJECT_DIR/pids/backend.pid")"
    [ -n "$pid" ]
}

@test "start (mock): all 3 components (be+fe+st) get real PIDs" {
    run bash "$MANAGE_SCRIPT" start
    [ "$status" -eq 0 ]
    for f in backend.pid frontend-user.pid studio.pid; do
        [ -f "$PROJECT_DIR/pids/$f" ]
        local pid
        pid="$(cat "$PROJECT_DIR/pids/$f")"
        [ -n "$pid" ]
        run kill -0 "$pid"
        [ "$status" -eq 0 ]
    done
}

@test "start (mock): no 'unbound variable' / 'ist nicht gesetzt' errors" {
    run bash "$MANAGE_SCRIPT" start
    [ "$status" -eq 0 ]
    [[ ! "$output" == *"ist nicht gesetzt"* ]]
    [[ ! "$output" == *"is unbound"* ]]
    [[ ! "$output" == *"unbound variable"* ]]
}

@test "start (mock): status shows all 3 components running" {
    bash "$MANAGE_SCRIPT" start >/dev/null 2>&1
    run bash "$MANAGE_SCRIPT" status
    [ "$status" -eq 0 ]
    [[ "$output" == *"backend:  running"* ]]
    [[ "$output" == *"frontend: running"* ]]
    [[ "$output" == *"studio:   running"* ]]
}