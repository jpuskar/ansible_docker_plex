#!/bin/bash
set -euo pipefail

echo "[terraria] tModLoader version: ${TMOD_VERSION:-unknown}"

CONFIG_PATH="/tmp/serverconfig.txt"
DATA_DIR="${DATA_DIR:-/data}"
SERVER_DIR="${SERVER_DIR:-/server}"

# ── Config generation ────────────────────────────────────────────────
if [ "${TMOD_USECONFIGFILE:-No}" = "Yes" ]; then
    if [ -f "${DATA_DIR}/serverconfig.txt" ]; then
        echo "[terraria] Using custom config from ${DATA_DIR}/serverconfig.txt"
        cp "${DATA_DIR}/serverconfig.txt" "${CONFIG_PATH}"
    else
        echo "[terraria] FATAL: TMOD_USECONFIGFILE=Yes but no serverconfig.txt found in ${DATA_DIR}/"
        exit 1
    fi
else
    "${SERVER_DIR}/prepare-config.sh" "${CONFIG_PATH}"
fi

# ── Mod downloading ──────────────────────────────────────────────────
if [ -n "${TMOD_AUTODOWNLOAD:-}" ]; then
    echo "[terraria] Downloading mods from Steam Workshop..."
    # Convert comma-separated IDs to steamcmd +workshop_download_item commands
    DOWNLOAD_ARGS=$(echo "${TMOD_AUTODOWNLOAD}" | sed 's/,/ +workshop_download_item 1281930 /g')
    steamcmd +force_install_dir "${DATA_DIR}/steamMods" \
        +login anonymous \
        +workshop_download_item 1281930 ${DOWNLOAD_ARGS} \
        +quit || echo "[terraria] WARNING: steamcmd exited with non-zero status"
    echo "[terraria] Mod download complete."
else
    echo "[terraria] No TMOD_AUTODOWNLOAD set — skipping mod download."
fi

# ── Mod enabling ─────────────────────────────────────────────────────
ENABLED_JSON="${DATA_DIR}/tModLoader/Mods/enabled.json"
WORKSHOP_DIR="${DATA_DIR}/steamMods/steamapps/workshop/content/1281930"

if [ -n "${TMOD_ENABLEDMODS:-}" ]; then
    echo "[terraria] Enabling mods..."
    mkdir -p "${DATA_DIR}/tModLoader/Mods"
    echo '[' > "${ENABLED_JSON}"

    echo "${TMOD_ENABLEDMODS}" | tr "," "\n" | while read -r MOD_ID; do
        [ -z "${MOD_ID}" ] && continue
        MOD_DIR=$(ls -d "${WORKSHOP_DIR}/${MOD_ID}/"*/ 2>/dev/null | tail -n 1)
        if [ -z "${MOD_DIR}" ]; then
            echo "[terraria] WARNING: Mod ${MOD_ID} not found in workshop cache"
            continue
        fi
        MOD_NAME=$(ls -1 "${MOD_DIR}" | sed -e 's/\.tmod$//' | head -n 1)
        echo "\"${MOD_NAME}\"," >> "${ENABLED_JSON}"
        echo "[terraria] Enabled: ${MOD_NAME} (${MOD_ID})"
    done

    echo ']' >> "${ENABLED_JSON}"
    echo "[terraria] Mod enabling complete."
else
    echo "[terraria] No TMOD_ENABLEDMODS set — using existing enabled.json if present."
fi

# ── Signal handling ──────────────────────────────────────────────────
pipe=/tmp/tmod.pipe

shutdown() {
    echo "[terraria] Shutting down server..."
    inject "say ${TMOD_SHUTDOWN_MESSAGE:-Server shutting down...}" 2>/dev/null || true
    sleep 3
    inject "exit" 2>/dev/null || true
    local tmux_pid
    tmux_pid=$(pgrep tmux 2>/dev/null) || true
    if [ -n "${tmux_pid}" ]; then
        while [ -e "/proc/${tmux_pid}" ]; do
            sleep 0.5
        done
    fi
    [ -e "${pipe}" ] && rm -f "${pipe}"
    echo "[terraria] Server stopped."
    exit 0
}

trap shutdown SIGINT SIGTERM

# ── Launch server ────────────────────────────────────────────────────
SERVER_CMD="${SERVER_DIR}/LaunchUtils/ScriptCaller.sh -server \
    -tmlsavedirectory \"${DATA_DIR}/tModLoader\" \
    -steamworkshopfolder \"${DATA_DIR}/steamMods/steamapps/workshop\" \
    -config \"${CONFIG_PATH}\""

echo "[terraria] Starting tModLoader server..."
echo "[terraria] Launch: ${SERVER_CMD}"

# Create tmux session with a pipe for docker log output
[ -e "${pipe}" ] && rm -f "${pipe}"
mkfifo "${pipe}"
sleep 2
tmux new-session -d "${SERVER_CMD} | tee ${pipe}"

# Kick the server to make sure it starts (tmux quirk)
sleep 3
inject "help" 2>/dev/null || true

# Start periodic autosave in background
"${SERVER_DIR}/autosave.sh" &

# Stream pipe to stdout for container logs
cat "${pipe}" &
wait $!
