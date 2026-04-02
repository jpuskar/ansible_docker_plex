#!/bin/bash
# Set GPU to Exclusive Process mode for MPS
# Run this with: talosctl -n k8s7 shell -- nvidia-smi -c EXCLUSIVE_PROCESS

echo "This script shows how to set GPU compute mode on Talos"
echo "MPS requires GPUs to be in EXCLUSIVE_PROCESS mode"
echo ""
echo "To set on Talos host, run:"
echo "  talosctl -n k8s7 shell -- nvidia-smi -c EXCLUSIVE_PROCESS"
echo ""
echo "Or create a DaemonSet that runs on boot to set it automatically"
