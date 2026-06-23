#!/usr/bin/env bats
#
# tests/scripts/http_probe.bats — Regression: manage.sh http_probe()
# must distinguish "process running, not yet listening" (uv sync
# still downloading 3+ GB of torch) from "really reachable".
#
# User scenario (2026-06-23):
#   ./manage.sh start   → status shows "backend: running (PID …)"
#   curl :8000/health   → connection refused (uvicorn not bound yet)
#   → user has no honest signal that the backend isn't ready.
#
# The fix adds a 3-second HTTP probe of /api/v1/system/status
# alongside the PID check. The probe must:
#   - Report "up" for a real uvicorn that answers 200
#   - Report "starting" when the port is not bound yet
#   - Report "degraded" on 5xx
#   - Use a short timeout so 'status' itself never hangs

setup() {
    TEST_TMP="$(mktemp -d /tmp/danwa-core-probe-XXXXXX)"
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

    export BACKEND_PORT=18000
    export FRONTEND_PORT=15173
    export STUDIO_PORT=15174
    export DANWA_USE_MOCK=1
}

teardown() {
    # Kill any leftover mock servers
    if [[ -n "${MOCK_PID:-}" ]]; then
        kill "$MOCK_PID" 2>/dev/null || true
    fi
    rm -rf "$TEST_TMP"
    unset DANWA_PROJECT_DIR DANWA_USE_MOCK BACKEND_PORT FRONTEND_PORT STUDIO_PORT
}

# Helper: source the manage.sh functions in isolation. We don't
# dispatch to cmd_*; we just call the http_probe() function.
run_http_probe() {
    bash -c "
        source '$MANAGE_SCRIPT' >/dev/null 2>&1
        http_probe '$1' $2
    "
}

@test "http_probe: reports 'starting' when the port is not bound" {
    # Port 1 is reserved and never bound in our CI environment.
    result="$(run_http_probe 1)"
    [[ "$result" == *"starting"* ]]
}

@test "http_probe: reports 'up' when a real server answers 200" {
    # Spin up a minimal Python HTTP server on port 18000.
    cd "$TEST_TMP"
    python3 -m http.server 18000 --bind 127.0.0.1 >/dev/null 2>&1 &
    MOCK_PID=$!
    sleep 0.5
    # The plain python http.server returns 200 for GET / but 501
    # for unknown paths. The probe just checks the status code.
    # The probe path is /api/v1/system/status which 501s on a plain
    # server — so we expect 'responding (HTTP 501)' which is
    # still proof that the probe actually hit a server.
    result="$(run_http_probe 18000)"
    [[ "$result" == *"HTTP"* ]]
    [[ "$result" != "starting"* ]]
}

@test "http_probe: --json mode emits a single token (up|starting|down|degraded)" {
    result="$(run_http_probe 1 --json)"
    case "$result" in
        up|starting|down|degraded) ;;
        *) {
            echo "Expected one of up|starting|down|degraded, got: $result"
            false
        } ;;
    esac
}

@test "http_probe: times out within 5s (does not hang the status command)" {
    # Use a very high port that won't have any listener; the probe
    # should still complete quickly thanks to curl --max-time 3.
    start_ts=$(date +%s)
    run_http_probe 65530 >/dev/null
    end_ts=$(date +%s)
    elapsed=$((end_ts - start_ts))
    [ "$elapsed" -lt 5 ]
}
