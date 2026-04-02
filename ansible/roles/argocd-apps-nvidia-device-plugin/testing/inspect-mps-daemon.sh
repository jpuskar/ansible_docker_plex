#!/bin/bash
# Inspect the MPS control daemon configuration

# Get the MPS daemon pod name
MPS_POD=$(kubectl get pods -n nvidia-device-plugin --no-headers | grep nvidia-device-plugin-mps-control-daemon | awk '{print $1}' | head -1)

if [ -z "$MPS_POD" ]; then
  echo "ERROR: Could not find MPS control daemon pod"
  exit 1
fi

echo "Found MPS daemon pod: $MPS_POD"
echo ""

echo "=== MPS Control Daemon Pod Spec ==="
kubectl get pod -n nvidia-device-plugin "$MPS_POD" -o yaml

echo ""
echo "=== Container Names ==="
kubectl get pod -n nvidia-device-plugin "$MPS_POD" -o jsonpath='{.spec.containers[*].name}' | tr ' ' '\n'

echo ""
echo ""
echo "=== Check MPS Daemon Logs ==="
echo "Available containers:"
kubectl get pod -n nvidia-device-plugin "$MPS_POD" -o jsonpath='{.spec.containers[*].name}' | tr ' ' '\n'
echo ""
echo "Logs from first container:"
kubectl logs -n nvidia-device-plugin "$MPS_POD" -c $(kubectl get pod -n nvidia-device-plugin "$MPS_POD" -o jsonpath='{.spec.containers[0].name}') --tail=30
