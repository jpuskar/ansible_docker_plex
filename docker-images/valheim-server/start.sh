#!/bin/bash
set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────
SERVER_NAME="${SERVER_NAME:-My Server}"
WORLD_NAME="${WORLD_NAME:-Dedicated}"
SERVER_PORT="${SERVER_PORT:-2456}"
SERVER_PASS="${SERVER_PASS:-}"
SERVER_PUBLIC="${SERVER_PUBLIC:-false}"
SERVER_ARGS="${SERVER_ARGS:-}"
CROSSPLAY="${CROSSPLAY:-false}"

# Translate true/false to 1/0 for the server binary
case "${SERVER_PUBLIC}" in
    true|1)  SERVER_PUBLIC=1 ;;
    *)       SERVER_PUBLIC=0 ;;
esac

# ── Log filter defaults ──────────────────────────────────────────────
# These match the community-valheim-tools defaults.
export VALHEIM_LOG_FILTER_EMPTY="${VALHEIM_LOG_FILTER_EMPTY:-true}"
export VALHEIM_LOG_FILTER_UTF8="${VALHEIM_LOG_FILTER_UTF8:-true}"
export VALHEIM_LOG_FILTER_MATCH="${VALHEIM_LOG_FILTER_MATCH:- }"
export VALHEIM_LOG_FILTER_STARTSWITH="${VALHEIM_LOG_FILTER_STARTSWITH:-(Filename:}"
export VALHEIM_LOG_FILTER_STARTSWITH_AssertionFailed="${VALHEIM_LOG_FILTER_STARTSWITH_AssertionFailed:-src/steamnetworkingsockets/clientlib/steamnetworkingsockets_lowlevel.cpp}"
VALHEIM_LOG_FILTER_VERBOSE="${VALHEIM_LOG_FILTER_VERBOSE:-2}"

# ── Admin/ban/permit lists ───────────────────────────────────────────
write_id_list() {
    local file="$1"
    local ids="$2"
    if [ -n "$ids" ]; then
        echo "Writing $file"
        # shellcheck disable=SC2086
        printf '%s\n' $ids > "$file"
    fi
}
write_id_list "/config/adminlist.txt"    "${ADMINLIST_IDS:-}"
write_id_list "/config/bannedlist.txt"   "${BANNEDLIST_IDS:-}"
write_id_list "/config/permittedlist.txt" "${PERMITTEDLIST_IDS:-}"

# ── Build server arguments ───────────────────────────────────────────
SERVER_CMD_ARGS=(
    -nographics
    -batchmode
    -name "${SERVER_NAME}"
    -port "${SERVER_PORT}"
    -world "${WORLD_NAME}"
    -public "${SERVER_PUBLIC}"
)

if [ -n "${SERVER_PASS}" ]; then
    SERVER_CMD_ARGS+=(-password "${SERVER_PASS}")
fi

if [ "${CROSSPLAY}" = "true" ]; then
    SERVER_CMD_ARGS+=(-crossplay)
fi

# Append any extra args the user passed
if [ -n "${SERVER_ARGS}" ]; then
    # shellcheck disable=SC2206
    SERVER_CMD_ARGS+=(${SERVER_ARGS})
fi

# ── Signal handling ──────────────────────────────────────────────────
# Valheim server saves the world on SIGINT, then exits.
server_pid=0

shutdown() {
    echo "[valheim] Received shutdown signal — sending SIGINT to server (PID ${server_pid})"
    if [ "${server_pid}" -gt 0 ]; then
        kill -INT "${server_pid}" 2>/dev/null || true
        wait "${server_pid}" 2>/dev/null || true
    fi
    echo "[valheim] Server stopped."
    exit 0
}

trap shutdown SIGINT SIGTERM

# ── Start ────────────────────────────────────────────────────────────
export SteamAppId=892970
export LD_LIBRARY_PATH="${SERVER_DIR}/linux64/"

echo "[valheim] Starting server: ${SERVER_NAME} | world: ${WORLD_NAME} | port: ${SERVER_PORT} | public: ${SERVER_PUBLIC}"

cd "${SERVER_DIR}"
chmod +x "${SERVER_DIR}/valheim_server.x86_64"

# Pipe server output through valheim-logfilter for cleaner logs
"${SERVER_DIR}/valheim_server.x86_64" "${SERVER_CMD_ARGS[@]}" \
    2>&1 | valheim-logfilter -logtostderr -v "${VALHEIM_LOG_FILTER_VERBOSE}" &
server_pid=$!

echo "[valheim] Server started with PID ${server_pid}"

# Wait for server to exit (or be signalled)
wait "${server_pid}"
exit_code=$?
echo "[valheim] Server exited with code ${exit_code}"
exit "${exit_code}"
