#!/usr/bin/env bash
# ref: https://forum.synology.com/enu/viewtopic.php?t=129633
# ref: https://github.com/Neilpang/acme.sh/wiki/Synology-NAS-Guide
set -e

# Purpose: Update Server certificates

# find certificates that might need an update
# cd / && find . -name "*.pem"

declare -a CERT_DIRS
CERT_DIRS=(\
        '/usr/syno/etc/certificate/AppPortal/VideoStation/' \
        '/usr/syno/etc/certificate/AppPortal/AudioStation/' \
        '/usr/syno/etc/certificate/AppPortal/FileStation/' \
        '/usr/syno/etc/certificate/AppPortal/DownloadStation/' \
        '/usr/local/etc/certificate/DirectoryServer/slapd/' \
        '/usr/local/etc/certificate/LogCenter/pkg-LogCenter/' \
        '/usr/local/etc/certificate/WebStation/vhost_225bb9ca-d884-44dd-a0f9-83ff557b95d6/' \
        '/usr/local/etc/certificate/CloudStation/CloudStationServer/' \
        '/usr/syno/etc/certificate/smbftpd/ftpd/' \
        '/usr/syno/etc/certificate/system/FQDN/' \
        '/usr/syno/etc/certificate/system/default/'
)

urlbase='https://cert.example.com/'
filebase='synology'
base="${urlbase}${filebase}"

mkdir -p ~/syno-cert

wget -nv -O ~/syno-cert/cert.pem "${base}.cer"
wget -nv -O ~/syno-cert/chain.pem "${base}.chain"
wget -nv -O ~/syno-cert/fullchain.pem "${base}.fullchain"

for d in "${CERT_DIRS[@]}"
do
        cp ~/syno-cert/cert.pem "$d"
        cp ~/syno-cert/chain.pem "$d"
        cp ~/syno-cert/fullchain.pem "$d"
done


# synoservicecfg --list
set +e
synoservicectl --reload pkgctl-WebStation
synoservicectl --reload pkgctl-LogCenter
synoservicectl --reload pkgctl-CloudStation
synoservicectl --reload pkgctl-DirectoryServer
synoservicectl --reload pkgctl-Git
synoservicectl --reload ftpd-ssl
synoservicectl --reload pkgctl-Git
synoservicectl --reload nginx
synoservicectl --reload ldap-server
set -e

rm -r ~/syno-cert

echo "Done"
