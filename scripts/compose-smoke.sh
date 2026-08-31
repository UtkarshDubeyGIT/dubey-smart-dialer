#!/bin/sh
set -eu

smoke_project="credresolve-smoke"
export SMART_DIALER_DB_PORT="${SMART_DIALER_SMOKE_DB_PORT:-55432}"
export SMART_DIALER_API_PORT="${SMART_DIALER_SMOKE_API_PORT:-58000}"

compose() {
    docker compose --project-name "$smoke_project" "$@"
}

cleanup() {
    smoke_status=$?
    trap - EXIT INT TERM
    if [ "$smoke_status" -ne 0 ]; then
        printf '%s\n' "[ERROR] Compose smoke test failed. Service status and logs follow."
        compose ps || true
        compose logs --no-color db api worker || true
    fi
    compose down --volumes --remove-orphans >/dev/null 2>&1 || true
    exit "$smoke_status"
}

trap cleanup EXIT INT TERM

printf '%s\n' "[INFO] Building and starting an isolated SmartDialer stack..."
compose up --build --detach --wait

printf '%s\n' "[INFO] Checking the public health endpoint..."
curl --fail --silent --show-error \
    "http://127.0.0.1:${SMART_DIALER_API_PORT}/health"
printf '\n'

printf '%s\n' "[INFO] Checking the reviewer dashboard..."
dashboard_page="$(curl --fail --silent --show-error \
    "http://127.0.0.1:${SMART_DIALER_API_PORT}/dashboard")"
if ! printf '%s' "$dashboard_page" | grep -q "SmartDialer Control Room"; then
    printf '%s\n' "[ERROR] Dashboard did not return the expected control-room page."
    exit 1
fi

printf '%s\n' "[INFO] Running one end-to-end pacing and worker demo..."
compose exec -T api smart-dialer seed-demo

printf '%s\n' "[INFO] Checking database access through the packaged CLI..."
compose exec -T api smart-dialer --json list-state

if ! compose ps --status running --services | grep -qx "worker"; then
    printf '%s\n' "[ERROR] Worker container is not running."
    exit 1
fi

printf '%s\n' "[OK] Compose smoke test passed: database, migrations, API, dashboard, CLI, and worker are healthy."
