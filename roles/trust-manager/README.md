# trust-manager Role

This role installs trust-manager on a Kubernetes cluster using Helm and automatically creates a Bundle to distribute CA certificates to all namespaces.

## Description

trust-manager is a Kubernetes operator that manages trust bundles (CA certificates) across the cluster. It distributes CA certificates to namespaces and makes them available to workloads.

This role automatically:
- Installs trust-manager via Helm
- Creates a Bundle that includes system CAs and the cert-manager root CA
- Distributes the CA bundle to all namespaces as a ConfigMap

## Requirements

- **cert-manager must be installed first** - trust-manager is an extension of cert-manager
- Kubernetes cluster must be accessible
- `kubectl` and `helm` must be installed on the Ansible controller
- `kubeconfig_path` variable must be set
- `temp_dir` variable must be set

## Role Variables

Available variables are listed below, along with default values (see `defaults/main.yml`):

```yaml
# trust-manager Helm chart configuration
trust_manager_version: "v0.14.1"
trust_manager_namespace: "cert-manager"
trust_manager_repo_url: "https://charts.jetstack.io"
trust_manager_chart_name: "jetstack/trust-manager"

# Installation options
trust_manager_create_namespace: false  # Usually installed in same namespace as cert-manager
trust_manager_replicas: 1

trust_manager_app_version: "v0.14.1"

# Bundle configuration for distributing CA certificates
trust_manager_deploy_bundle: true
trust_manager_bundle_name: "ca-bundle"
trust_manager_bundle_configmap_name: "ca-certificates"
trust_manager_bundle_configmap_key: "ca-bundle.crt"

# CA certificate to include in bundle (from cert-manager)
trust_manager_ca_secret_name: "root-ca-secret"
trust_manager_ca_secret_key: "ca.crt"
```

### Bundle Configuration

When `trust_manager_deploy_bundle` is `true` (default), the role will:

1. Create a Bundle named `ca-bundle`
2. Include default system CAs (from the container image)
3. Include the cert-manager root CA from the secret `root-ca-secret`
4. Sync the combined CA bundle to all namespaces as a ConfigMap named `ca-certificates`

Applications can mount this ConfigMap to trust both public CAs and your internal CA.

## Dependencies

- cert-manager role (must be applied first)

## Example Playbook

```yaml
---
- hosts: localhost
  vars:
    kubeconfig_path: "/path/to/kubeconfig"
    temp_dir: "/tmp/ansible"
  roles:
    - cert-manager
    - trust-manager
```

## Post-Installation

The role automatically creates a Bundle that distributes CA certificates to all namespaces. The CA bundle is available in every namespace as a ConfigMap named `ca-certificates`.

### Using the CA Bundle in Pods

Mount the CA bundle ConfigMap in your pods:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: my-app
spec:
  containers:
  - name: app
    image: my-app:latest
    volumeMounts:
    - name: ca-certs
      mountPath: /etc/ssl/certs/ca-bundle.crt
      subPath: ca-bundle.crt
      readOnly: true
    env:
    - name: SSL_CERT_FILE
      value: /etc/ssl/certs/ca-bundle.crt
  volumes:
  - name: ca-certs
    configMap:
      name: ca-certificates
```

### Creating Additional Bundles

You can create additional Bundle resources for specific use cases:

```yaml
apiVersion: trust.cert-manager.io/v1alpha1
kind: Bundle
metadata:
  name: custom-bundle
spec:
  sources:
  # Include a certificate from a Secret
  - secret:
      name: my-custom-ca
      key: ca.crt
  target:
    configMap:
      key: "custom-ca.crt"
    namespaceSelector:
      matchLabels:
        custom-ca: enabled
```

## Verification

Check that trust-manager is running:

```bash
kubectl get pods -n cert-manager --kubeconfig /path/to/kubeconfig | grep trust-manager
```

List available Bundles:

```bash
kubectl get bundles --kubeconfig /path/to/kubeconfig
```

Check the Bundle status:

```bash
kubectl describe bundle ca-bundle --kubeconfig /path/to/kubeconfig
```

Verify the ConfigMap is created in all namespaces:

```bash
# Check in default namespace
kubectl get configmap ca-certificates -n default --kubeconfig /path/to/kubeconfig

# List all ca-certificates ConfigMaps across all namespaces
kubectl get configmap ca-certificates --all-namespaces --kubeconfig /path/to/kubeconfig
```

View the CA bundle contents:

```bash
kubectl get configmap ca-certificates -n default \
  --kubeconfig /path/to/kubeconfig \
  -o jsonpath='{.data.ca-bundle\.crt}' | head -20
```

## Common Use Cases

1. **Distributing internal CA certificates** to all pods
2. **Adding custom root CAs** to the trust store
3. **Combining multiple CA bundles** into a single trust bundle
4. **Automatic trust bundle updates** when CA certificates are rotated

## References

- [trust-manager Documentation](https://cert-manager.io/docs/trust/trust-manager/)
- [trust-manager Helm Chart](https://artifacthub.io/packages/helm/cert-manager/trust-manager)
