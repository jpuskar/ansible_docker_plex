# OpenEBS Rawfile CSI Role

This role installs OpenEBS Rawfile LocalPV CSI driver on a Kubernetes cluster, configured to only run on nodes with TPM encryption.

## Description

OpenEBS Rawfile LocalPV is a Container Storage Interface (CSI) driver that provides local persistent volumes using raw files on the host filesystem. Unlike other local storage solutions, it creates actual files rather than relying on block devices.

**Key restriction**: This role configures the CSI driver to **only deploy on nodes labeled with `encryption.talos.dev/tpm: "true"`**. This ensures storage is only provisioned on nodes with TPM-based LUKS encryption.

## Requirements

- Kubernetes cluster must be accessible
- `kubectl` and `helm` must be installed on the Ansible controller
- `kubeconfig_path` variable must be set
- `temp_dir` variable must be set
- **Nodes must have TPM encryption labels** (set by the talos-gen-configs role)
- At least one node with `encryption.talos.dev/tpm: "true"` must exist

## Role Variables

Available variables are listed below (see `defaults/main.yml`):

```yaml
# OpenEBS Rawfile CSI configuration
openebs_rawfile_version: "0.9.0"
openebs_rawfile_namespace: "openebs-rawfile"
openebs_rawfile_repo_url: "https://openebs.github.io/rawfile-localpv"
openebs_rawfile_chart_name: "openebs-rawfile/rawfile-csi"

# Storage path on nodes
openebs_rawfile_storage_path: "/var/openebs/rawfile"

# Node selector - only deploy on nodes with TPM encryption
openebs_rawfile_node_selector:
  encryption.talos.dev/tpm: "true"

# StorageClass configuration
openebs_rawfile_storageclass_name: "openebs-rawfile"
openebs_rawfile_storageclass_is_default: false
openebs_rawfile_filesystem: "ext4"
```

## Dependencies

- **talos-gen-configs role** - Must be run first to apply node labels

## Example Playbook

```yaml
---
- hosts: localhost
  vars:
    kubeconfig_path: "/path/to/kubeconfig"
    temp_dir: "/tmp/ansible"
  roles:
    - openebs-rawfile
```

## How It Works

1. **Node Selection**: The CSI driver pods (controller and node plugins) are scheduled only on nodes with `encryption.talos.dev/tpm: "true"`
2. **Storage Topology**: The StorageClass uses `allowedTopologies` to ensure volumes are only provisioned on TPM-encrypted nodes
3. **Local Storage**: Each node stores raw files in `/var/openebs/rawfile` (configurable)
4. **Volume Binding**: Uses `WaitForFirstConsumer` to ensure volumes are created on the same node where the pod is scheduled

## Post-Installation

### Verify Installation

Check that rawfile CSI is running:

```bash
kubectl get pods -n openebs-rawfile --kubeconfig /path/to/kubeconfig
```

You should see:
- `rawfile-csi-controller-*` (running on TPM node)
- `rawfile-csi-node-*` (one per TPM node)

Check StorageClass:

```bash
kubectl get storageclass openebs-rawfile --kubeconfig /path/to/kubeconfig
```

Verify node topology:

```bash
kubectl get storageclass openebs-rawfile -o yaml | grep -A 5 allowedTopologies
```

### Creating a PersistentVolumeClaim

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: my-data
  namespace: default
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: openebs-rawfile
  resources:
    requests:
      storage: 10Gi
```

Apply it:

```bash
kubectl apply -f pvc.yaml --kubeconfig /path/to/kubeconfig
```

### Using in a Pod

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: my-app
  namespace: default
spec:
  containers:
  - name: app
    image: nginx
    volumeMounts:
    - name: data
      mountPath: /data
  volumes:
  - name: data
    persistentVolumeClaim:
      claimName: my-data
```

The pod will be scheduled on a TPM-encrypted node with the PVC provisioned on the same node.

## Storage Path

By default, volumes are stored at `/var/openebs/rawfile` on each node. You can customize this:

```yaml
openebs_rawfile_storage_path: "/mnt/storage/rawfile"
```

**Important**: Ensure the path exists and has sufficient space on your nodes.

## Verification Commands

### Check which nodes are eligible

```bash
kubectl get nodes -l encryption.talos.dev/tpm=true --kubeconfig /path/to/kubeconfig
```

### List PersistentVolumes

```bash
kubectl get pv --kubeconfig /path/to/kubeconfig
```

### Check volume location on node

SSH to the node (or use Talos):
```bash
ls -lh /var/openebs/rawfile/
```

### View CSI driver logs

```bash
# Controller logs
kubectl logs -n openebs-rawfile deployment/rawfile-csi-controller --kubeconfig /path/to/kubeconfig

# Node plugin logs
kubectl logs -n openebs-rawfile daemonset/rawfile-csi-node --kubeconfig /path/to/kubeconfig
```

## Node Topology Constraints

The StorageClass enforces topology constraints:

```yaml
allowedTopologies:
- matchLabelExpressions:
  - key: encryption.talos.dev/tpm
    values:
    - "true"
```

This means:
- Volumes will **only** be created on nodes with TPM encryption
- Pods using these PVCs must be scheduled on TPM-encrypted nodes
- Non-TPM nodes (like k8s4) will never host rawfile volumes

## Troubleshooting

### No eligible nodes

**Error**: PVC stuck in `Pending` state

**Check**:
```bash
kubectl describe pvc my-data
# Look for: "waiting for first consumer to be created"
```

**Solution**: Ensure at least one node has the TPM label:
```bash
kubectl get nodes -l encryption.talos.dev/tpm=true
```

### CSI pods not starting

**Check node selector**:
```bash
kubectl get pods -n openebs-rawfile -o wide
```

If no pods are running, verify nodes have the required label.

### Volume provisioning fails

**Check controller logs**:
```bash
kubectl logs -n openebs-rawfile deployment/rawfile-csi-controller
```

Common issues:
- Storage path doesn't exist on node
- Insufficient disk space
- Permission issues

### Pod won't schedule

If a pod with a rawfile PVC won't schedule:

```bash
kubectl describe pod my-app
# Look for: "no nodes match pod topology spread constraints"
```

The pod must be scheduled on a TPM-encrypted node due to volume topology.

## Comparison with Other Storage Solutions

| Feature | Rawfile LocalPV | hostPath | Local PV |
|---------|-----------------|----------|----------|
| Dynamic provisioning | ✅ | ❌ | ❌ |
| Volume expansion | ✅ | ❌ | ❌ |
| Snapshots | ✅ | ❌ | ❌ |
| Node topology aware | ✅ | ❌ | ✅ |
| No pre-provisioning | ✅ | ✅ | ❌ |

## Security Considerations

1. **TPM Encryption**: By restricting to TPM nodes, all data is stored on encrypted volumes
2. **Local Storage**: Data never leaves the node (good for compliance)
3. **Path Isolation**: Each PV gets its own directory
4. **No Network**: No network-based attack surface

## References

- [OpenEBS Rawfile LocalPV GitHub](https://github.com/openebs/rawfile-localpv)
- [OpenEBS Documentation](https://openebs.io/docs)
- [Kubernetes CSI Documentation](https://kubernetes-csi.github.io/docs/)
- [Storage Topology](https://kubernetes.io/docs/concepts/storage/storage-classes/#allowed-topologies)
