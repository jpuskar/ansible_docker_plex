# KubeVirt operational cheat sheet

All commands assume k8s2:

```bash
export KUBECONFIG=~/.kube/k8s2-config
```

VMs live in `vms-windows` (or whatever VM namespace you've created). Examples
below use `win2016` as the VM name.

## VM lifecycle (virtctl)

```bash
# Graceful shutdown — ACPI power button press (Windows runs its shutdown)
virtctl stop win2016 -n vms-windows

# Force off — like yanking the cord. Use only if the OS is wedged.
virtctl stop win2016 -n vms-windows --grace-period=0 --force

# Start
virtctl start win2016 -n vms-windows

# Reboot (graceful)
virtctl restart win2016 -n vms-windows

# Pause / unpause (CPU frozen, RAM preserved)
virtctl pause   vmi win2016 -n vms-windows
virtctl unpause vmi win2016 -n vms-windows
```

Status:

```bash
kubectl get vm,vmi -n vms-windows
```

## Console access

VNC via local TigerVNC (much smoother than the browser viewer):

```bash
virtctl vnc win2016 -n vms-windows \
  --vnc-path=/Applications/TigerVNC.app/Contents/MacOS/vncviewer \
  --vnc-type=tiger
```

Serial console (Linux guests; Windows shows EFI shell only):

```bash
virtctl console win2016 -n vms-windows
```

## Ctrl-Alt-Del

TigerVNC's F8 menu does not work on macOS, and there is no Ctrl-Alt-Del shortcut
in the viewer. Inject it via libvirt inside the virt-launcher pod:

```bash
POD=$(kubectl get pod -n vms-windows -l kubevirt.io/vm=win2016 \
  -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n vms-windows "$POD" -c compute -- \
  virsh send-key vms-windows_win2016 KEY_LEFTCTRL KEY_LEFTALT KEY_DELETE
```

The libvirt domain name is `<namespace>_<vm-name>`, so for the `win2016` VM in
`vms-windows` it is `vms-windows_win2016`.

Other useful keystrokes (same `send-key` form):

| Combo                | Key codes                                          |
|----------------------|----------------------------------------------------|
| Ctrl-Alt-Del         | `KEY_LEFTCTRL KEY_LEFTALT KEY_DELETE`              |
| Ctrl-Alt-F1..F6 (TTY)| `KEY_LEFTCTRL KEY_LEFTALT KEY_F1`                  |
| Alt-Tab              | `KEY_LEFTALT KEY_TAB`                              |
| Print Screen         | `KEY_SYSRQ`                                        |
| Windows key          | `KEY_LEFTMETA`                                     |

## Quick discovery

```bash
# All VMs across the cluster
kubectl get vm,vmi -A

# Where is a VM running?
kubectl get vmi -n vms-windows -o wide

# DataVolume status (CDI imports)
kubectl get dv -n vms-windows

# What host devices is k8s8 advertising?
kubectl get node k8s8 -o jsonpath='{.status.allocatable}' \
  | jq 'with_entries(select(.key | startswith("ups.local") or startswith("devices.kubevirt.io")))'
```
