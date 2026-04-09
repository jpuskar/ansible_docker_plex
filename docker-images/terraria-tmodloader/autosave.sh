#!/bin/bash
while true; do
    sleep "${TMOD_AUTOSAVE_INTERVAL:-10}m"
    echo "[terraria] Auto-saving world..."
    inject "save" 2>/dev/null || true
done
