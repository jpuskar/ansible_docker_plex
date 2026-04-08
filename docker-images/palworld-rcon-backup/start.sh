#!/bin/bash
set -euo pipefail

if [[ -z "${MY_NAMESPACE:-}" ]]; then
  echo "ERROR: MY_NAMESPACE env var is not set. Exiting."
  exit 1
fi

echo "Waiting 30s for server to start."
sleep 30

while true; do
    set +e
    python3 /run-backup.py --secret-namespace "$MY_NAMESPACE"
    if [ $? -eq 0 ]; then
        echo "Command executed successfully. Waiting for 5mins before saving again."
        # Sleep for 5 minutes before running the command again
        sleep 300  # Sleep for 5 minutes (300 seconds)
    else
        echo "Command failed, waiting for 1 minute before retrying..."
        sleep 60  # Wait for 1 minute (60 seconds)
    fi
    set -e

done
