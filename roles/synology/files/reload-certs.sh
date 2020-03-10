#!/bin/bash
set -euo pipefail
# script to reload synology certificates for all assigned services
# it is meant to be run after renewing certificats to reload all affected services
# for example form acme.sh as --reloadcmd

#ref: https://raw.githubusercontent.com/bartowl/synology-stuff/master/reload-certs.sh

NEW_CERT_ROOT_PATH="/volume6/le_certs"
DOMAIN_ID="EhzQLd"
CURRENT_CERT_ROOT_PATH="/usr/syno/etc/certificate/_archive/${DOMAIN_ID}"
INFO="/usr/syno/etc/certificate/_archive/INFO"

## This can help find the correct cert path.
domain=$(jq -r ".$DOMAIN_ID.desc" "${INFO}");
echo "domain: ${domain}"
num_services=$(jq -r ".$DOMAIN_ID.services|length" "${INFO}")
echo "num_services: ${num_services}"
src_path="${CURRENT_CERT_ROOT_PATH}/${DOMAIN_ID}"
echo "src_path: ${src_path}"

new_key_path="${NEW_CERT_ROOT_PATH}/cert_for_services.key"
new_fullchain_path="${NEW_CERT_ROOT_PATH}/fullchain.pem"
new_cert_path="${NEW_CERT_ROOT_PATH}/cert.pem"

current_key_path="${NEW_CERT_ROOT_PATH}/privkey.pem"
current_fullchain_path="${NEW_CERT_ROOT_PATH}/cert_for_services.fullchain"
current_cert_path="${NEW_CERT_ROOT_PATH}/cert_for_services.fullchain"

update='false'
if ! diff --brief "${new_key_path}" "${current_key_path}" > /dev/null; then
    cat "${new_key_path}" > "${current_key_path}"
    update='true'
fi

if ! diff --brief "${new_key_path}" "${current_fullchain_path}" > /dev/null; then
    cat "${new_fullchain_path}" > "${current_key_path}"
    update='true'
fi

if ! diff --brief "${new_key_path}" "${current_cert_path}" > /dev/null; then
    cat "${new_cert_path}" > "${current_key_path}"
    update='true'
fi

if [[ "${update}" == "true" ]]; then
    for srv_id in $(seq 0 $((num_services-1))); do
        name=$(jq -r ".$DOMAIN_ID.services[$srv_id].display_name" ${INFO})
        service=$(jq -r ".$DOMAIN_ID.services[$srv_id].service" ${INFO})
        subscriber=$(jq -r ".$DOMAIN_ID.services[$srv_id].subscriber" ${INFO})
        is_pkg=$(jq -r ".$DOMAIN_ID.services[$srv_id].isPkg" ${INFO})
        if [[ "${is_pkg}" == "true" ]]; then
            crtpath="/usr/local/etc/certificate/${subscriber}/${service}"
            reload="/usr/local/libexec/certificate.d/${subscriber}"
        else
            crtpath="/usr/syno/etc/certificate/${subscriber}/${service}"
            reload="/usr/libexec/certificate.d/${subscriber}"
        fi
        [[ -x "$reload" ]] || reload=/bin/true
        # check service CRT gainst src_path
        if ! diff -q "${crtpath}/cert.pem" "${src_path}/cert.pem" > /dev/null; then
            echo "* updating certificate for ${name}"
            for f in cert.pem chain.pem fullchain.pem privkey.pem; do
                cat "${src_path}/${f}" > "${crtpath}/${f}"
            done
            echo "reloading..."
            $reload "${service}" > /dev/null
        fi
  done
fi
