#!/bin/bash
set -eo pipefail

# ref https://www.naschenweng.info/2017/01/06/securing-ubiquiti-unifi-cloud-key-encrypt-automatic-dns-01-challenge/
echo "** Configuring new Let's Encrypt certs"
NEW_CERT_FOLDER="/home/pfSenseCertCopier/new_certs"
CUR_MD5SUM=$(md5sum /etc/ssl/private/cloudkey.crt | cut -d ' ' -f 1)
NEW_MD5SUM=$(md5sum "${NEW_CERT_FOLDER}/wildcard.crt" | cut -d ' ' -f 1)
if [[ "${CUR_MD5SUM}" == "${NEW_MD5SUM}" ]]; then
   echo "Skipping cert update because md5 has not changed"
   exit 0
fi

BACKUP_DATESTAMP=$(date +"%Y%m%d_%H%M%S")
pushd /etc/ssl/private
tar -czvf "${HOME}/${BACKUP_DATESTAMP}.tar" ./
popd

TMP_PATH=$(mktemp -d)
openssl pkcs12 \
  -export \
  -in "${NEW_CERT_FOLDER}/wildcard.crt" \
  -inkey "${NEW_CERT_FOLDER}/wildcard.key" \
  -out "${TMP_PATH}/cloudkey.p12" \
  -name unifi \
  -password pass:aircontrolenterprise

keytool \
  -importkeystore \
  -deststorepass aircontrolenterprise \
  -destkeypass aircontrolenterprise \
  -destkeystore "${TMP_PATH}/unifi.keystore.jks" \
  -srckeystore "${TMP_PATH}/cloudkey.p12" \
  -srcstoretype PKCS12 \
  -srcstorepass aircontrolenterprise \
  -alias unifi

cp "${NEW_CERT_FOLDER}/wildcard.crt" /etc/ssl/private/cloudkey.crt
cp "${NEW_CERT_FOLDER}/wildcard.key" /etc/ssl/private/cloudkey.key
cp "${TMP_PATH}/unifi.keystore.jks" /etc/ssl/private/unifi.keystore.jks
md5sum -b /etc/ssl/private/unifi.keystore.jks > /etc/ssl/private/unifi.keystore.jks.md5

chown root:ssl-cert /etc/ssl/private/*
chmod 640 /etc/ssl/private/*

echo "** Testing Nginx and restarting"
/usr/sbin/nginx -t
systemctl restart unifi
systemctl restart nginx
