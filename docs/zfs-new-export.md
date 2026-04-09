# Adding a New ZFS NFS Export for Kubernetes

## 1. ZFS Server Side

### Create the dataset
```shell
sudo zfs create tank0/<dataset-name>
sudo zfs set mountpoint=/tank0/<dataset-name> tank0/<dataset-name>
sudo zfs set quota=100G tank0/<dataset-name>           # adjust as needed
zfs list -o name,used,avail,mountpoint,quota tank0/<dataset-name>
```

### Set ownership and permissions

Pick (or create) a UID/GID that matches what the pod will run as.
For example, Harbor registry runs as GID `2020`.

> **NFS ignores `fsGroup`** — the container process must actually run with
> the correct UID/GID, and the export directory must be owned accordingly.

```shell
# Use the UID:GID that the pod's securityContext will use.
# Example for Velero (runs as root 0:0 by default):
sudo chown 0:0 /tank0/<dataset-name>

# Example for Harbor registry (runs as 10000:2020):
sudo chown 10000:2020 /tank0/k8s2-harbor
```

Permissions should be `0770` (owner + group, no world access).
**Never use `0755`** — that gives world-read + world-execute, which leaks
data to any user on the ZFS host and to any UID inside any pod that mounts it.

```shell
sudo chmod 0770 /tank0/<dataset-name>
```

| Permission | When to use |
|------------|-------------|
| `0770`     | Default — owner + group full access, no world |
| `0700`     | Single-user service, no group needed |
| ~~`0755`~~ | **Never** — world-readable NFS exports are a security risk |

### Share via NFS
```shell
# List every k8s node IP separated by colons
sudo zfs set sharenfs="rw=10.0.x.y:10.0.x.y:10.0.x.y:10.0.x.y:10.0.x.y:10.0.x.y,sync,no_subtree_check" tank0/<dataset-name>
```

### Firewall
```shell
sudo ufw allow from 192.168.x.y/32 to any port 2049 proto tcp
sudo ufw allow from 192.168.x.y/32 to any port 2049 proto udp
```

Verify the export is visible:
```shell
showmount -e localhost
```

---

## 2. Kubernetes Side

Three objects are needed to consume the NFS export in k8s. See the Harbor
role for a working reference:
- [roles/argocd-apps-harbor/templates/nfs-storageclass.yaml](../ansible/roles/argocd-apps-harbor/templates/nfs-storageclass.yaml)
- [roles/argocd-apps-harbor/templates/registry-nfs-pv.yaml](../ansible/roles/argocd-apps-harbor/templates/registry-nfs-pv.yaml)

### a) StorageClass (one per app, manual provisioner)

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: <app>-nfs
provisioner: kubernetes.io/no-provisioner
volumeBindingMode: WaitForFirstConsumer
reclaimPolicy: Retain
```

### b) PersistentVolume

```yaml
apiVersion: v1
kind: PersistentVolume
metadata:
  name: <app>-nfs
spec:
  capacity:
    storage: 100Gi                     # match the ZFS quota
  accessModes:
    - ReadWriteMany
  persistentVolumeReclaimPolicy: Retain
  storageClassName: <app>-nfs          # must match the StorageClass
  nfs:
    server: <nfs-server-ip>
    path: /tank0/<dataset-name>
  mountOptions:
    - hard
    - nfsvers=4.1
    - noatime
```

### c) PersistentVolumeClaim

The PVC is usually created by the Helm chart when you set the matching
`storageClass` in its values. If the chart doesn't handle it, create one
manually:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: <app>-nfs
  namespace: <app-namespace>
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: <app>-nfs
  volumeName: <app>-nfs               # binds to the specific PV
  resources:
    requests:
      storage: 100Gi
```

### d) SecurityContext / GID considerations

NFS does **not** honor Kubernetes `fsGroup`. The container must run with a
UID/GID that has actual filesystem permissions on the export.

- Set `runAsUser` / `runAsGroup` in the pod's `securityContext` to match the
  `chown` on the ZFS server.
- If the Helm chart doesn't expose these, use a filter plugin or
  `kustomize`-style post-patch (see Harbor's
  [harbor.py filter](../ansible/roles/argocd-apps-harbor/filter_plugins/harbor.py)
  for an example).

---

## Quick Checklist

- [ ] ZFS dataset created with correct mountpoint and quota
- [ ] `chown <uid>:<gid>` matches the pod's `runAsUser`/`runAsGroup`
- [ ] `chmod 0770` (not 0755!)
- [ ] `sharenfs` set with all k8s node IPs
- [ ] UFW rules allow TCP+UDP 2049 from the k8s node subnet
- [ ] `showmount -e` confirms the export is visible
- [ ] StorageClass, PV (and PVC if needed) templated in the argocd-apps role
- [ ] Mount options include `hard`, `nfsvers=4.1`, `noatime`
