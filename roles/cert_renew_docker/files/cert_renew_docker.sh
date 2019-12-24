#!/bin/bash
set -eo pipefail

# TODO: test certs /w openssl first in case they're corrupted or only partially copied

echo "** Configuring new Let's Encrypt certs"
NEW_CERT_FOLDER="/home/pfSenseCertCopier/new_certs"
if [[ -f "/etc/ssl/private/wildcard.crt" ]]; then
  CUR_MD5SUM=$(md5sum /etc/ssl/private/wildcard.crt | cut -d ' ' -f 1)
  NEW_MD5SUM=$(md5sum "${NEW_CERT_FOLDER}/wildcard.crt" | cut -d ' ' -f 1)
  if [[ "${CUR_MD5SUM}" == "${NEW_MD5SUM}" ]]; then
     echo "Skipping cert update because md5 has not changed"
     exit 0
  fi
fi
BACKUP_DATESTAMP=$(date +"%Y%m%d_%H%M%S")
pushd /etc/ssl/private
tar -czvf "${HOME}/${BACKUP_DATESTAMP}.tar" ./
popd

cp "${NEW_CERT_FOLDER}/wildcard.crt" /etc/ssl/private/wildcard.crt
cp "${NEW_CERT_FOLDER}/wildcard.key" /etc/ssl/private/wildcard.key

chown root:docker /etc/ssl/private/*
chmod 640 /etc/ssl/private/*
