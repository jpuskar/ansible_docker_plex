# lib-vscode-remote-statefulset

Library role for deploying VS Code remote development containers as Kubernetes StatefulSets with SSH access.

## Purpose

This is a **library role** that provides the templates and logic for deploying VS Code remote containers. It should not be called directly - instead, use an implementation role like `argocd-apps-vscode-remote-standard`.

## What This Role Does

- Templates Kubernetes manifests for:
  - Namespace
  - SSH authorized keys Secret
  - StatefulSet with persistent volumes
  - LoadBalancer Service for SSH access

## Required Variables

- `vscode_instance_name`: Unique name for the instance
- `vscode_namespace`: Kubernetes namespace
- `vscode_image`: Container image to use
- `vscode_ssh_authorized_keys`: List of SSH public keys
- `output_dir_path`: Where to write manifests

## Optional Variables

See `defaults/main.yml` for all optional variables and their defaults.

## Building the Docker Image

Reference Dockerfile and build scripts are in the `references/` directory.

### Basic Build

```bash
cd references/
./build.sh
docker push your-registry.com/vscode-remote:latest
```

### Customizing Packages

Edit the Dockerfile to add the packages you need. For example, for Python development:

```dockerfile
RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    curl \
    # ... existing packages ...
    python3 \
    python3-pip \
    python3-venv \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
```

For Go development:

```dockerfile
RUN apt-get update && apt-get install -y \
    # ... existing packages ...
    golang-go \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
```

Then build with a specific tag:

```bash
docker build -t your-registry.com/vscode-remote:python .
docker push your-registry.com/vscode-remote:python
```

**Note:** Packages are baked into the image at build time, not installed at runtime. This makes container startup faster and more reliable.

## Usage

This role should be included by implementation roles. Example:

```yaml
- name: Deploy VS Code Remote
  include_role:
    name: lib-vscode-remote-statefulset
  vars:
    vscode_instance_name: "{{ my_instance_name }}"
    vscode_namespace: "{{ my_namespace }}"
    vscode_image: "{{ my_image }}"
    vscode_ssh_authorized_keys: "{{ my_ssh_keys }}"
    output_dir_path: "{{ my_output_dir }}"
```

## Implementation Roles

- `argocd-apps-vscode-remote-standard`: Standard VS Code remote environment

## Reference Files

The `references/` directory contains:

- `Dockerfile`: Container image definition
- `entrypoint.sh`: Container startup script
- `build.sh`: Build helper script
- `example-playbook.yml`: Usage examples

## Features

- **Fully Persistent Storage**:
  - Workspace volume for project files
  - Home directory volume (includes .vscode-server, venvs, configs, shell history, etc.)
  - Survives pod restarts/crashes - reconnect to the same environment
- **SSH Access**: Key-based authentication for VS Code Remote-SSH
- **Resource Limits**: Configurable CPU/memory
- **Customizable**: Install packages, set environment variables
- **Multi-Instance**: Deploy multiple independent instances

## Persistent Volumes

Each instance creates two PersistentVolumeClaims:

1. **workspace**: Project files and code (`vscode_storage_size`, default 50Gi)
   - Mounted at `/workspace`
   - Your main development directory

2. **home**: User home directory (`vscode_home_size`, default 20Gi)
   - Mounted at `/home/vscode`
   - Contains:
     - `.vscode-server`: VS Code Server binaries and extensions
     - `.local`: Python venvs, pip packages, local binaries
     - `.cache`: Build caches, package caches
     - `.config`: Tool configurations
     - `.ssh`: SSH keys and config
     - Shell history and configurations

This means when you reconnect (even after pod crashes), everything is preserved:
- Installed VS Code extensions
- Python virtual environments
- Shell history and configurations
- Git configurations
- All dotfiles and user data
