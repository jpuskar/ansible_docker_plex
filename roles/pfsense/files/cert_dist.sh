#!/usr/local/bin/bash
set -Eeuo pipefail

CERT_NAME="cert_for_services"

SRC="/conf/acme"
DEST="/home/cert_puller/certs/${CERT_NAME}"

mkdir -p "${DEST}"
chmod 600 "${DEST}"
chown cert_puller:certpullers "${DEST}"

echo "Copying key from (${SRC}/${CERT_NAME}.key) to (${DEST}/wildcard.key)"
cp "${SRC}/${CERT_NAME}.key" "${DEST}/wildcard.key"
chmod 600 "${DEST}/wildcard.key"
chown cert_puller:certpullers "${DEST}/wildcard.key"

echo "Copying chain from (${SRC}/${CERT_NAME}.fullchain) to (${DEST}/wildcard.crt)"
cp "${SRC}/${CERT_NAME}.fullchain" "${DEST}/wildcard.crt"
chmod 600 "${DEST}/wildcard.crt"
chown cert_puller:certpullers "${DEST}/wildcard.crt"
#!/usr/local/bin/bash
set -Eeuo pipefail

CERT_NAME="cert_for_services"

SRC="/conf/acme"
DEST="/home/cert_puller/certs/${CERT_NAME}"

mkdir -p "${DEST}"
chmod 600 "${DEST}"
chown cert_puller:certpullers "${DEST}"

echo "Copying key from (${SRC}/${CERT_NAME}.key) to (${DEST}/wildcard.key)"
cp "${SRC}/${CERT_NAME}.key" "${DEST}/wildcard.key"
chmod 600 "${DEST}/wildcard.key"
chown cert_puller:certpullers "${DEST}/wildcard.key"

echo "Copying chain from (${SRC}/${CERT_NAME}.fullchain) to (${DEST}/wildcard.crt)"
cp "${SRC}/${CERT_NAME}.fullchain" "${DEST}/wildcard.crt"
chmod 600 "${DEST}/wildcard.crt"
chown cert_puller:certpullers "${DEST}/wildcard.crt"
