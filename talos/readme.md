
# Talos notes

# Non-manual runs
```shell
ansible-playbook playbooks/generate-k8s2-talos-config.yml -e talos_luks_passphrase=asdf
```

Then: `cd tmp/talos/k8s2`.
Then:
```shell
NODES="192.168.25.151"
talosctl --talosconfig=./talosconfig config endpoints ${NODES}
talosctl apply-config --insecure --file controlplane.yaml --nodes ${NODES}
talosctl bootstrap --talosconfig=./talosconfig --nodes ${NODES}
talosctl kubeconfig --talosconfig=./talosconfig --nodes ${NODES}
```

# Notes for manual runs
factory.talos.dev/metal-installer/0b92dc99db71715e1a269eddc014ef6bd37d5d36263a8921fde74e6e427b6a20:v1.11.5


https://github.com/siderolabs/talos/releases/download/v1.11.6/talosctl-linux-amd64


export CLUSTER_NAME=k8s2-node1
export DISK_NAME=sda
export CONTROL_PLANE_IP=192.168.1.196

```shell
talosctl gen secrets -o secrets.yaml
```

```shell
talosctl gen config \
    $CLUSTER_NAME \
    https://$CONTROL_PLANE_IP:6443 \
    --install-disk /dev/$DISK_NAME \
    --additional-sans "k8s2.{{ fqdn }}" \
    --with-secrets secrets.yaml
```

talosctl --talosconfig=./talosconfig config endpoints $CONTROL_PLANE_IP
talosctl apply-config --insecure --nodes $CONTROL_PLANE_IP --file controlplane.yaml
talosctl bootstrap --nodes $CONTROL_PLANE_IP --talosconfig=./talosconfig
talosctl kubeconfig --nodes $CONTROL_PLANE_IP --talosconfig=./talosconfig

# Secure Boot

## Dell Optiplex 7050

1. Write talos image in DD mode with Rufus.
2. Boot to BIOS
   1. Enable secure boot 'custom mode' and then delete all keys.
   2. Enable UEFI updates from USB.
3. F12 boot to USB and you should see 'Enroll Keys: Auto'.


# TODO
- add SANs
- get box on the correct VLAN
