#!/bin/bash
set -e

BUILDER_NAME="arm64-builder"

echo "Setting up Docker buildx for ARM64 cross-compilation..."
echo ""

# Check if QEMU is needed (not on ARM64 host)
if [ "$(uname -m)" != "aarch64" ]; then
    echo "Installing QEMU for ARM64 emulation..."
    docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
    echo "✓ QEMU installed"
else
    echo "✓ Running on ARM64, no emulation needed"
fi

echo ""

# Check if builder already exists
if docker buildx ls | grep -q "^${BUILDER_NAME}"; then
    echo "Builder '${BUILDER_NAME}' already exists"

    # Check if it's active
    if docker buildx ls | grep "^${BUILDER_NAME}" | grep -q "\*"; then
        echo "✓ Builder '${BUILDER_NAME}' is already active"
    else
        echo "Switching to builder '${BUILDER_NAME}'..."
        docker buildx use "${BUILDER_NAME}"
        echo "✓ Switched to builder '${BUILDER_NAME}'"
    fi
else
    echo "Creating builder '${BUILDER_NAME}'..."
    docker buildx create \
        --name "${BUILDER_NAME}" \
        --driver docker-container \
        --use
    echo "✓ Builder '${BUILDER_NAME}' created"
fi

echo ""

# Bootstrap the builder (download buildkit image)
echo "Bootstrapping builder..."
docker buildx inspect --bootstrap

echo ""
echo "✓ Setup complete!"
echo ""
echo "You can now build ARM64 images with:"
echo "  ./build.sh"
echo ""
echo "Or manually:"
echo "  docker buildx build --platform linux/arm64 -t your-image ."
