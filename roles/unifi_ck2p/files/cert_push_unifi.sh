#!/bin/sh
scp -i ~/.ssh/id_ed25519 \
  /conf/acme/cert_for_services.key  \
  pfSenseCertCopier@192.168.2.5:~/new_certs/wildcard.key

scp -i ~/.ssh/id_ed25519 \
  /conf/acme/cert_for_services.fullchain \
  pfSenseCertCopier@192.168.2.5:~/new_certs/wildcard.crt
