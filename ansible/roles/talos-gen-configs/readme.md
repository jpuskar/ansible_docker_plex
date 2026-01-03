# Image Selection System

This role uses a tag-based image selection system to automatically choose the appropriate Talos installation image for each node.

## How It Works

1. **Image Registry** (`defaults/main.yml`): Defines available Talos images with their properties (tags)
2. **Requirements** (per-host or global): Each node specifies what it needs via `talos_image_requirements`
3. **Automatic Selection**: The first image matching all requirements is selected

## Defining Images

Images are defined in `defaults/main.yml` under `talos_images_info`. Each image has:
- `name`: Human-readable identifier
- `image`: Full image URL for `talosctl --install-image`
- `tags`: Metadata for matching
  - `secureboot`: true/false
  - `version`: Talos version (e.g., "v1.12.0")
  - `extensions`: List of system extensions included (e.g., ["intel-ucode"])

Example:
```yaml
talos_images_info:
  - name: "v1.12.0-secureboot-intel-ucode"
    image: "factory.talos.dev/installer-secureboot/2d61dd07b20062062ea671b4d01873506103b67c0f7a4c3fb6cf4ee85585dcb8:v1.12.0"
    tags:
      secureboot: true
      version: "v1.12.0"
      extensions:
        - intel-ucode
```

## Specifying Requirements

### Global Default (defaults/main.yml)
```yaml
talos_image_requirements:
  secureboot: true
  version: "v1.11.5"
  extensions: []
```

### Per-Host Override (inventory/host_vars/hostname.yml)
```yaml
talos_image_requirements:
  secureboot: false  # This node doesn't support SecureBoot
  version: "v1.11.5"
  extensions: []
```

## Matching Logic

The role selects the **first image** where:
- `secureboot` matches exactly (if specified in requirements)
- `version` matches exactly (if specified in requirements)
- ALL required `extensions` are present in the image (subset match)

If no image matches, the task will fail with an undefined variable error.

## Example Configurations

### Standard node with SecureBoot
```yaml
# host_vars/k8s2.yml
talos_image_requirements:
  secureboot: true
  version: "v1.11.5"
  extensions: []
```

### Node without SecureBoot support
```yaml
# host_vars/k8s4.yml
talos_enable_luks: false
talos_image_requirements:
  secureboot: false
  version: "v1.11.5"
  extensions: []
```

### Node requiring Intel microcode
```yaml
# host_vars/special-node.yml
talos_image_requirements:
  secureboot: true
  version: "v1.12.0"
  extensions:
    - intel-ucode
```

## Node Labels

The role automatically applies labels to each node based on its encryption configuration. These labels allow you to target specific nodes (e.g., for CSI storage classes).

### Encryption Labels

Each node receives the following labels:

- `encryption.talos.dev/enabled`: "true" or "false" - Whether LUKS encryption is enabled
- `encryption.talos.dev/tpm`: "true" or "false" - Whether TPM is used for encryption
- `encryption.talos.dev/type`: Encryption type used:
  - `luks-tpm`: LUKS with TPM unlock
  - `luks-passphrase`: LUKS with passphrase only
  - `none`: No encryption
- `node.kubernetes.io/instance-name`: Node hostname

### Example Usage with CSI

Target nodes with TPM encryption for storage classes:

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: encrypted-storage
provisioner: your-csi-driver
parameters:
  encrypted: "true"
allowedTopologies:
- matchLabelExpressions:
  - key: encryption.talos.dev/tpm
    values:
    - "true"
```

View node labels:

```bash
kubectl get nodes --show-labels
kubectl get nodes -l encryption.talos.dev/tpm=true
```

## Available Images

### 1.12.0 - SecureBoot with Intel Microcode

Customization:
```yaml
systemExtensions:
  officialExtensions:
    - siderolabs/intel-ucode
```

ISO: https://factory.talos.dev/image/2d61dd07b20062062ea671b4d01873506103b67c0f7a4c3fb6cf4ee85585dcb8/v1.12.0/metal-amd64-secureboot.iso

Image: `factory.talos.dev/installer-secureboot/2d61dd07b20062062ea671b4d01873506103b67c0f7a4c3fb6cf4ee85585dcb8:v1.12.0`
