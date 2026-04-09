#!/bin/bash
set -euo pipefail

# Constants
PVC_NAME="zomboid-dedicated-server-data"
NAMESPACE="zomboid"
LABEL_SELECTOR="app=zomboid-dedicated-server"
BACKUP_DIR="/backup"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/zomboid_backup_${TIMESTAMP}.tar.gz"
MIN_SIZE_MB=100
RCON_COMMAND="/usr/local/bin/rcon -a localhost:27015 -p \"\${RCON_PASSWORD}\" save"
DELAY_AFTER_SAVE=10  # Seconds to wait after saving
SAVE_SUCCESS=true  # Default to true, will change if RCON fails

# Ensure MicroK8s kubectl is available
if ! command -v /snap/bin/microk8s &>/dev/null; then
    echo "Error: MicroK8s is not installed or not in PATH."
    exit 1
fi

# Get the first pod matching the label
POD_NAME=$(/snap/bin/microk8s kubectl get pods -n "$NAMESPACE" -l "$LABEL_SELECTOR" -o jsonpath='{.items[0].metadata.name}' || echo "")

if [[ -z "$POD_NAME" ]]; then
    echo "Error: No pod found matching labels '$LABEL_SELECTOR' in namespace '$NAMESPACE'."
    exit 1
fi

echo "Found pod: $POD_NAME"

# Run the save command inside the pod, allow failure
echo "Running RCON save command inside pod $POD_NAME..."
if ! /snap/bin/microk8s kubectl exec -n "$NAMESPACE" "$POD_NAME" -- /bin/bash -c "$RCON_COMMAND"; then
    echo "Warning: RCON save command failed inside pod $POD_NAME. Proceeding with backup anyway."
    SAVE_SUCCESS=false
fi

echo "Waiting $DELAY_AFTER_SAVE seconds before backup..."
sleep "$DELAY_AFTER_SAVE"

# Get the Persistent Volume (PV) bound to the PVC
PV_NAME=$(/snap/bin/microk8s kubectl get pvc "$PVC_NAME" -n "$NAMESPACE" -o jsonpath='{.spec.volumeName}')

if [[ -z "$PV_NAME" ]]; then
    echo "Error: PVC '$PVC_NAME' not found or not bound to a PV in namespace '$NAMESPACE'."
    exit 1
fi

# Get the hostPath from the PV definition
HOST_PATH=$(/snap/bin/microk8s kubectl get pv "$PV_NAME" -o jsonpath='{.spec.hostPath.path}')

if [[ -z "$HOST_PATH" ]]; then
    echo "Error: HostPath not found for PV '$PV_NAME'."
    exit 1
fi

# Ensure the host path exists
if [[ ! -d "$HOST_PATH" ]]; then
    echo "Error: HostPath '$HOST_PATH' does not exist on this node."
    exit 1
fi

# Print found host path
echo "HostPath for PVC '$PVC_NAME': $HOST_PATH"

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

# Adjust backup filename if save failed
if [[ "$SAVE_SUCCESS" == "false" ]]; then
    BACKUP_FILE="${BACKUP_FILE%.tar.gz}-tainted.tar.gz"
fi

# Create a tar backup
tar -czvf "$BACKUP_FILE" -C "$HOST_PATH" .

# Verify backup size
BACKUP_SIZE_MB=$(( $(stat -c%s "$BACKUP_FILE") / 1024 / 1024 ))

if [[ "$BACKUP_SIZE_MB" -lt "$MIN_SIZE_MB" ]]; then
    echo "Warning: Backup file is only ${BACKUP_SIZE_MB}MB, which is less than the expected ${MIN_SIZE_MB}MB."
else
    echo "Backup successful: $BACKUP_FILE (${BACKUP_SIZE_MB}MB)"
fi
