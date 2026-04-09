#!/bin/bash
set -euo pipefail

CONFIG_PATH="${1:-/tmp/serverconfig.txt}"
DATA_DIR="${DATA_DIR:-/data}"

echo "[terraria] Generating server config at ${CONFIG_PATH}"

# Start fresh
> "${CONFIG_PATH}"

# World
TMOD_WORLDNAME="${TMOD_WORLDNAME:-Docker}"
echo "world=${DATA_DIR}/tModLoader/Worlds/${TMOD_WORLDNAME}.wld" >> "${CONFIG_PATH}"
echo "worldpath=${DATA_DIR}/tModLoader/Worlds/" >> "${CONFIG_PATH}"

if [ ! -f "${DATA_DIR}/tModLoader/Worlds/${TMOD_WORLDNAME}.wld" ]; then
    echo "[terraria] World '${TMOD_WORLDNAME}' not found — will be auto-created."
    echo "worldname=${TMOD_WORLDNAME}" >> "${CONFIG_PATH}"
    echo "autocreate=${TMOD_WORLDSIZE:-3}" >> "${CONFIG_PATH}"
fi

# Password
if [ "${TMOD_PASS:-}" != "N/A" ] && [ -n "${TMOD_PASS:-}" ]; then
    echo "password=${TMOD_PASS}" >> "${CONFIG_PATH}"
fi

# Server settings
echo "motd=${TMOD_MOTD:-A tModLoader server}" >> "${CONFIG_PATH}"
echo "maxplayers=${TMOD_MAXPLAYERS:-8}" >> "${CONFIG_PATH}"
echo "seed=${TMOD_WORLDSEED:-}" >> "${CONFIG_PATH}"
echo "difficulty=${TMOD_DIFFICULTY:-1}" >> "${CONFIG_PATH}"
echo "secure=${TMOD_SECURE:-0}" >> "${CONFIG_PATH}"
echo "language=${TMOD_LANGUAGE:-en-US}" >> "${CONFIG_PATH}"
echo "npcstream=${TMOD_NPCSTREAM:-60}" >> "${CONFIG_PATH}"
echo "upnp=${TMOD_UPNP:-0}" >> "${CONFIG_PATH}"
echo "priority=${TMOD_PRIORITY:-1}" >> "${CONFIG_PATH}"
echo "port=${TMOD_PORT:-7777}" >> "${CONFIG_PATH}"

# Journey mode permissions
echo "journeypermission_time_setfrozen=${TMOD_JOURNEY_SETFROZEN:-0}" >> "${CONFIG_PATH}"
echo "journeypermission_time_setdawn=${TMOD_JOURNEY_SETDAWN:-0}" >> "${CONFIG_PATH}"
echo "journeypermission_time_setnoon=${TMOD_JOURNEY_SETNOON:-0}" >> "${CONFIG_PATH}"
echo "journeypermission_time_setdusk=${TMOD_JOURNEY_SETDUSK:-0}" >> "${CONFIG_PATH}"
echo "journeypermission_time_setmidnight=${TMOD_JOURNEY_SETMIDNIGHT:-0}" >> "${CONFIG_PATH}"
echo "journeypermission_godmode=${TMOD_JOURNEY_GODMODE:-0}" >> "${CONFIG_PATH}"
echo "journeypermission_wind_setstrength=${TMOD_JOURNEY_WIND_STRENGTH:-0}" >> "${CONFIG_PATH}"
echo "journeypermission_rain_setstrength=${TMOD_JOURNEY_RAIN_STRENGTH:-0}" >> "${CONFIG_PATH}"
echo "journeypermission_time_setspeed=${TMOD_JOURNEY_TIME_SPEED:-0}" >> "${CONFIG_PATH}"
echo "journeypermission_rain_setfrozen=${TMOD_JOURNEY_RAIN_FROZEN:-0}" >> "${CONFIG_PATH}"
echo "journeypermission_wind_setfrozen=${TMOD_JOURNEY_WIND_FROZEN:-0}" >> "${CONFIG_PATH}"
echo "journeypermission_increaseplacementrange=${TMOD_JOURNEY_PLACEMENT_RANGE:-0}" >> "${CONFIG_PATH}"
echo "journeypermission_setdifficulty=${TMOD_JOURNEY_SET_DIFFICULTY:-0}" >> "${CONFIG_PATH}"
echo "journeypermission_biomespread_setfrozen=${TMOD_JOURNEY_BIOME_SPREAD:-0}" >> "${CONFIG_PATH}"
echo "journeypermission_setspawnrate=${TMOD_JOURNEY_SPAWN_RATE:-0}" >> "${CONFIG_PATH}"

echo "[terraria] Config generation complete."
