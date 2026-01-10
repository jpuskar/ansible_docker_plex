#!/bin/bash
# Verify CDI is properly configured and being used by containerd

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <gpu-node-ip>"
    exit 1
fi

NODE_IP="$1"

echo "========================================="
echo "CDI Configuration Verification"
echo "Node: $NODE_IP"
echo "========================================="
echo ""

echo "1. Check if CDI spec file exists:"
talosctl -n "$NODE_IP" ls /var/cdi/dynamic/
echo ""

echo "2. Check containerd config for CDI settings:"
talosctl -n "$NODE_IP" read /etc/cri/conf.d/20-customization.part || echo "Config file not found - CDI not enabled!"
echo ""

echo "3. List available CDI devices (from spec):"
talosctl -n "$NODE_IP" sh -c "grep -A 2 'name:.*gpu' /var/cdi/dynamic/nvidia.yaml | head -20" || echo "Cannot read CDI spec"
echo ""

echo "4. Check containerd is aware of CDI (requires containerd restart after config change):"
talosctl -n "$NODE_IP" service containerd status
echo ""

echo "5. Look for CDI entries in containerd config dump:"
talosctl -n "$NODE_IP" sh -c "crictl info 2>/dev/null | grep -i cdi" || echo "Note: crictl info might not show CDI config directly"
echo ""

echo "========================================="
echo "Next Steps:"
echo "========================================="
echo "If CDI config exists but containerd hasn't restarted:"
echo "  - Apply the updated Talos machine config with nvidia.yaml changes"
echo "  - This will restart containerd automatically"
echo ""
echo "To test if pods are using CDI:"
echo "  1. Create a test pod with runtimeClassName: nvidia"
echo "  2. Check pod logs for CUDA errors"
echo "  3. Inspect pod with: kubectl describe pod <pod-name>"
echo ""
