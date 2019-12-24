#!/bin/bash
set -uo pipefail
# No -e becase we want to coninue on scp failure.

# TODO: better error on fingerprint wrong/changed vs a timeout
# TODO: shorter timeout on ssh can't connect
# TODO: do the copies concurrently

ID_FILE="${HOME}/.ssh/id_ed25519"
CERT_NAME="cert_for_services"

HOSTS=(
  "192.168.2.5"
  "192.168.3.11"
  "192.168.3.12"
  "192.168.2.149"
  "192.168.2.150"
)

for HOSTNAME in "${HOSTS[@]}"; do
    echo "Copying key to ${HOSTNAME}"
    scp -i "${ID_FILE}" \
      "/conf/acme/${CERT_NAME}.key"  \
      "pfSenseCertCopier@${HOSTNAME}:~/new_certs/wildcard.key"

    echo "Copying crt to ${HOSTNAME}"
    scp -i "${ID_FILE}" \
      "/conf/acme/${CERT_NAME}.fullchain" \
      "pfSenseCertCopier@${HOSTNAME}:~/new_certs/wildcard.crt"
done
