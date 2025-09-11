
# Overview

1. OPNSense
   1. LetsEncrypt
   2. VPN
   3. DNS
   4. Backups
   5. Firewall Rules
2. NAS
   1. LUKS + autofs
   2. node-exporter
   3. ZFS / zpools
   4. Udev rules
   5. Scrub cronjob
   6. NFS
   7. SMB
   8. snapshots
3. talos k8s
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
4. Camera Server
   1. microk8s
   2. metallb
   3. ingress
   4. shinobi


# Sequence and Secrets Handling

1. OpnSense
   1. Create USB and install pfsense
   2. Initial user and ansible password
   3. Run ansible to configure 
