
# Overview

1. OPNSense
   1. LetsEncrypt
   2. VPN
   3. DHCP
   4. DNS
   5. Backups
   6. Firewall Rules
   7. NTP
2. Hardware
   1. Misc Notes
      1. SFPs and DACs (which work, 30m vs 100m for RJ45 / heat)
   2. Dell switches
      1. login and commands
   3. Arista Switches
      1. login and commands
      2. vlan membership
   4. Unifi Switches
      1. login and commands
      2. upgrade via cli
      3. inform url
3. NAS
   1. LUKS + autofs
       1. notes on sector alignment for 512e drives
   2. node-exporter
   3. ZFS / zpools
   4. Udev rules
   5. Scrub cronjob
   6. NFS
   7. SMB
   8. snapshots
4. talos k8s
   1. cert-manager
   2. metrics-server and prom
   3. metallb
   4. ingress
   5. secret-generator
   6. argocd
   7. alerta
   8. csi-driver-nfs
   9. plex
   10. unifi
   11. godaddy-ddns
   12. game servers
   13. falcon
   14. opa
5. Camera Server
   1. microk8s
   2. metallb
   3. ingress
   4. shinobi
      1. Dockerfile builds
6. Camera configs
   1. Where to find firmware
   2. Default settings
   3. Unintuitive settings


# Sequence and Secrets Handling

1. OpnSense
   1. Create USB and install pfsense
   2. Initial user and ansible password
   3. Run ansible to configure 

# ZFS Snapshots
zfs snapshot tank0/plex@manual-$(date +%Y%m%d-%H%M)
