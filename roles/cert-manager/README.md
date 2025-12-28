# cert-manager Role

This role installs cert-manager on a Kubernetes cluster using Helm and optionally deploys a self-signed root CA.

## Description

cert-manager adds certificates and certificate issuers as resource types in Kubernetes clusters, and simplifies the process of obtaining, renewing and using those certificates.

This role automatically:
- Installs cert-manager via Helm
- Creates a self-signed root CA (optional, enabled by default)
- Creates a ClusterIssuer that uses the root CA for signing certificates

## Requirements

- Kubernetes cluster must be accessible
- `kubectl` and `helm` must be installed on the Ansible controller
- `kubeconfig_path` variable must be set
- `temp_dir` variable must be set

## Role Variables

Available variables are listed below, along with default values (see `defaults/main.yml`):

```yaml
# cert-manager Helm chart configuration
cert_manager_version: "v1.16.2"
cert_manager_namespace: "cert-manager"
cert_manager_repo_url: "https://charts.jetstack.io"
cert_manager_chart_name: "jetstack/cert-manager"

# Installation options
cert_manager_install_crds: true
cert_manager_create_namespace: true

# Configuration
cert_manager_enable_prometheus: true
cert_manager_replicas: 1

# Self-signed CA configuration
cert_manager_deploy_ca: true
cert_manager_ca_name: "root-ca"
cert_manager_ca_secret_name: "root-ca-secret"
cert_manager_ca_issuer_name: "ca-issuer"
cert_manager_ca_common_name: "Internal Root CA"
cert_manager_ca_organization: "Internal"
cert_manager_ca_duration: "87600h"  # 10 years
cert_manager_ca_renew_before: "720h"  # 30 days
```

### Self-Signed CA

When `cert_manager_deploy_ca` is `true` (default), the role will:

1. Create a `selfsigned-issuer` ClusterIssuer (self-signed)
2. Create a root CA Certificate named `root-ca` in the cert-manager namespace
3. Create a `ca-issuer` ClusterIssuer that uses the root CA

The root CA certificate is stored in a secret named `root-ca-secret` and can be used by trust-manager to distribute the CA to all namespaces.

## Dependencies

None. However, trust-manager requires cert-manager to be installed first.

## Example Playbook

```yaml
---
- hosts: localhost
  vars:
    kubeconfig_path: "/path/to/kubeconfig"
    temp_dir: "/tmp/ansible"
  roles:
    - cert-manager
```

## Post-Installation

After installation, you can use the automatically created `ca-issuer` ClusterIssuer to issue certificates, or create additional ClusterIssuers.

### Using the Internal CA

The role automatically creates a `ca-issuer` ClusterIssuer. Use it to issue certificates:

```yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: my-app-cert
  namespace: default
spec:
  secretName: my-app-tls
  issuerRef:
    name: ca-issuer
    kind: ClusterIssuer
  commonName: my-app.example.com
  dnsNames:
    - my-app.example.com
    - www.my-app.example.com
```

### Adding Let's Encrypt

You can also add Let's Encrypt for public certificates:

```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: your-email@example.com
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
    - http01:
        ingress:
          class: nginx
```

## Verification

Check that cert-manager is running:

```bash
kubectl get pods -n cert-manager --kubeconfig /path/to/kubeconfig
```

## References

- [cert-manager Documentation](https://cert-manager.io/docs/)
- [cert-manager Helm Chart](https://artifacthub.io/packages/helm/cert-manager/cert-manager)
