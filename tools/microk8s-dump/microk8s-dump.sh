#!/bin/bash
set -euo pipefail
OUTDIR="./k8s-full-dump-$(date +%Y%m%d-%H%M%S)"
mkdir -p "${OUTDIR}/namespaced" "${OUTDIR}/cluster"

# Dump all namespaced resources
for resource in $(kubectl api-resources --verbs=list --namespaced=true -o name 2>/dev/null); do
  echo "Dumping namespaced: ${resource}"
  kubectl get "${resource}" --all-namespaces -o yaml > "${OUTDIR}/namespaced/${resource//\//__}.yaml" 2>/dev/null || true
done

# Dump all cluster-scoped resources (includes CRDs themselves)
for resource in $(kubectl api-resources --verbs=list --namespaced=false -o name 2>/dev/null); do
  echo "Dumping cluster-scoped: ${resource}"
  kubectl get "${resource}" -o yaml > "${OUTDIR}/cluster/${resource//\//__}.yaml" 2>/dev/null || true
done

echo "Done. Output in ${OUTDIR}"
tar czf "${OUTDIR}.tar.gz" "${OUTDIR}"
