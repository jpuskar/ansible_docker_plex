
# Talos notes

# Non-manual runs

Find what disk you want to use:
```shell
talosctl get disks --nodes $IP_ADDRESS --insecure
```

```shell
ansible-playbook playbooks/generate-k8s2-talos-config.yml -e talos_luks_passphrase=asdf
```

Then: `cd tmp/talos/k8s2`.
Then:
```shell
NODES="192.168.x.y"
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
export CONTROL_PLANE_IP=192.168.x.y

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


# Upgrade
```shell
talosctl --talosconfig ./talosconfig --nodes $IP_ADDRESS upgrade --image ghcr.io/siderolabs/installer:v1.12.0
talosctl --talosconfig ./talosconfig --nodes $IP_ADDRESS reboot
```


# Etcd issues
```shell
talosctl --talosconfig ./talosconfig --nodes $IP_ADDRESS etcd members
talosctl --talosconfig ./talosconfig --nodes $IP_ADDRESS etcd remove-member <ID>
```


# Secure Boot

## Dell Optiplex 7050
1. Upgrade TPM from 1.2 to 2.0. There is a TPM update tool for this.
2. Write talos image in DD mode with Rufus.
3. Boot to BIOS
   1. Enable secure boot 'custom mode' and then delete all keys.
   2. Enable UEFI updates from USB.
4. F12 boot to USB and you should see 'Enroll Keys: Auto'.


## HP Compaq Elite notes

### Compaq Elite 8200 and 8300
NOTE: Does not seem to work with Talos 1.11.6 with LUKS enabled. I got a blinking cursor immediately after POST.
NOTE: Does not support SecureBoot
Bios notes:
1. Upgrade BIOS to 2.33
2. Reset defaults
3. Apply defaults and reboot
4. Open BIOS and configure.
   1. Security -> Devices -> Hide Security Device (TPM)
   2. Advanced -> Option ROMs -> Disable



# TODO
- add SANs
- get box on the correct VLAN
