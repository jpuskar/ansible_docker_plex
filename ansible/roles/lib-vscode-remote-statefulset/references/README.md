# Building VS Code Remote Container Image

This directory contains the Dockerfile and build scripts for the VS Code remote development container.

## Quick Start

### 1. One-Time Setup (x86_64 host building for ARM64)

```bash
./setup-buildx.sh
```

This script:
- Installs QEMU for ARM64 emulation
- Creates a buildx builder named `arm64-builder`
- Bootstraps the builder

### 2. Build the Image

```bash
./build.sh
```

Or with custom settings:

```bash
IMAGE_NAME=vscode-dev IMAGE_TAG=v1.0 REGISTRY=ghcr.io/myorg ./build.sh
```

### 3. Push to Registry

```bash
docker push ghcr.io/your-org/vscode-remote:latest
```

Or build and push in one step:

```bash
docker buildx build --platform linux/arm64 --push -t ghcr.io/your-org/vscode-remote:latest .
```

## Customizing the Image

Edit `Dockerfile` to add packages. The image includes:
- Python 3 + pip + venv
- Git, curl, wget, vim
- SSH server (runs on port 2222)
- Common dev tools (tmux, htop, jq, etc.)

For GPU/ML work, install PyTorch/HuggingFace via pip in your environment - the nvidia runtime provides GPU access.

## Troubleshooting

**Builder not found:**
```bash
./setup-buildx.sh
```

**QEMU not working:**
```bash
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
```

**Remove and recreate builder:**
```bash
docker buildx rm arm64-builder
./setup-buildx.sh
```
