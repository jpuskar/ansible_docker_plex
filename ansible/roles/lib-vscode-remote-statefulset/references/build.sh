#!/bin/bash
set -e

# Configuration - override with env vars
: "${IMAGE_NAME:=vscode-remote}"
: "${IMAGE_TAG:=latest}"
: "${REGISTRY:=ghcr.io/your-org}"

# Full image name
FULL_IMAGE="${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"

# Check if buildx is set up for ARM64
BUILDER_NAME="arm64-builder"
if ! docker buildx ls | grep -q "^${BUILDER_NAME}"; then
    echo "ERROR: Builder '${BUILDER_NAME}' not found!"
    echo ""
    echo "Run this first:"
    echo "  ./setup-buildx.sh"
    echo ""
    exit 1
fi

# Make sure we're using the right builder
docker buildx use "${BUILDER_NAME}" 2>/dev/null || true

echo "Building Docker image: ${FULL_IMAGE} (ARM64)"
echo ""

# Use buildx for cross-platform build
docker buildx build \
    --platform linux/arm64 \
    --load \
    -t "${FULL_IMAGE}" \
    .

echo ""
echo "Build complete!"
echo ""
echo "To push to registry:"
echo "  docker push ${FULL_IMAGE}"
echo ""
echo "To use in your playbook, set:"
echo "  vscode_image: ${FULL_IMAGE}"
