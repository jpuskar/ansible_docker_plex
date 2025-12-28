# ArgoCD Role

This role installs ArgoCD on a Kubernetes cluster using Helm.

## Description

ArgoCD is a declarative, GitOps continuous delivery tool for Kubernetes. It automates the deployment of applications by monitoring Git repositories and synchronizing the desired state with the cluster.

This role automatically:
- Installs ArgoCD via Helm
- Configures server, controller, and repo server components
- Optionally enables ingress for web UI access
- Retrieves and displays admin credentials

## Requirements

- Kubernetes cluster must be accessible
- `kubectl` and `helm` must be installed on the Ansible controller
- `kubeconfig_path` variable must be set
- `temp_dir` variable must be set

## Role Variables

Available variables are listed below, along with default values (see `defaults/main.yml`):

```yaml
# ArgoCD Helm chart configuration
argocd_version: "7.7.17"  # Chart version
argocd_app_version: "v2.13.4"  # ArgoCD application version
argocd_namespace: "argocd"
argocd_repo_url: "https://argoproj.github.io/argo-helm"
argocd_chart_name: "argo/argo-cd"

# Installation options
argocd_create_namespace: true

# Server configuration
argocd_server_replicas: 1
argocd_server_ingress_enabled: false
argocd_server_ingress_host: "argocd.example.com"
argocd_server_insecure: false  # Set to true to disable TLS

# Controller configuration
argocd_controller_replicas: 1

# Repo server configuration
argocd_repo_server_replicas: 1

# Redis configuration
argocd_redis_enabled: true

# Dex (SSO) configuration
argocd_dex_enabled: true

# ApplicationSet controller
argocd_applicationset_enabled: true
argocd_applicationset_replicas: 1

# Notifications controller
argocd_notifications_enabled: true

# High availability mode
argocd_ha_enabled: false

# Metrics and monitoring
argocd_metrics_enabled: true
```

## Dependencies

None. However, if you enable ingress, you may want cert-manager installed first for TLS certificates.

## Example Playbook

### Basic Installation

```yaml
---
- hosts: localhost
  vars:
    kubeconfig_path: "/path/to/kubeconfig"
    temp_dir: "/tmp/ansible"
  roles:
    - argocd
```

### With Ingress Enabled

```yaml
---
- hosts: localhost
  vars:
    kubeconfig_path: "/path/to/kubeconfig"
    temp_dir: "/tmp/ansible"
    argocd_server_ingress_enabled: true
    argocd_server_ingress_host: "argocd.mydomain.com"
  roles:
    - cert-manager
    - argocd
```

### High Availability Setup

```yaml
---
- hosts: localhost
  vars:
    kubeconfig_path: "/path/to/kubeconfig"
    temp_dir: "/tmp/ansible"
    argocd_ha_enabled: true
    argocd_server_replicas: 3
    argocd_controller_replicas: 3
    argocd_repo_server_replicas: 3
  roles:
    - argocd
```

## Post-Installation

### Accessing ArgoCD UI

#### Via Port Forward (Default)

```bash
kubectl port-forward svc/argocd-server -n argocd 8080:443 --kubeconfig /path/to/kubeconfig
```

Then open: https://localhost:8080

#### Via Ingress (If Enabled)

Open your browser to: https://argocd.mydomain.com

### Login Credentials

The role will display the admin credentials after installation. You can also retrieve them:

```bash
# Username is always: admin

# Get password
kubectl get secret argocd-initial-admin-secret \
  -n argocd \
  --kubeconfig /path/to/kubeconfig \
  -o jsonpath='{.data.password}' | base64 -d
```

### ArgoCD CLI

Install the ArgoCD CLI:

```bash
# Linux
curl -sSL -o argocd https://github.com/argoproj/argo-cd/releases/latest/download/argocd-linux-amd64
chmod +x argocd
sudo mv argocd /usr/local/bin/

# macOS
brew install argocd
```

Login with the CLI:

```bash
# Via port-forward
argocd login localhost:8080

# Via ingress
argocd login argocd.mydomain.com
```

### Creating Your First Application

Create an Application manifest:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/myorg/myapp
    targetRevision: HEAD
    path: kubernetes
  destination:
    server: https://kubernetes.default.svc
    namespace: default
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
    - CreateNamespace=true
```

Apply it:

```bash
kubectl apply -f my-app.yaml --kubeconfig /path/to/kubeconfig
```

Or create via CLI:

```bash
argocd app create my-app \
  --repo https://github.com/myorg/myapp \
  --path kubernetes \
  --dest-server https://kubernetes.default.svc \
  --dest-namespace default
```

## Verification

Check that ArgoCD is running:

```bash
kubectl get pods -n argocd --kubeconfig /path/to/kubeconfig
```

Check ArgoCD server status:

```bash
kubectl get svc -n argocd --kubeconfig /path/to/kubeconfig
```

List applications:

```bash
argocd app list
```

## Common Operations

### Change Admin Password

```bash
argocd account update-password
```

### Create a Project

```bash
argocd proj create myproject \
  --description "My project" \
  --src https://github.com/myorg/* \
  --dest https://kubernetes.default.svc,*
```

### Sync an Application

```bash
argocd app sync my-app
```

### View Application Details

```bash
argocd app get my-app
```

### Delete an Application

```bash
argocd app delete my-app
```

## Security Considerations

1. **Change the default admin password** immediately after installation
2. **Enable ingress with TLS** for production use
3. **Configure RBAC** to limit user access
4. **Use SSO** (via Dex) for multi-user environments
5. **Store sensitive data** (like repo credentials) in Kubernetes secrets

## Troubleshooting

### Pods not starting

Check pod logs:
```bash
kubectl logs -n argocd deployment/argocd-server --kubeconfig /path/to/kubeconfig
```

### Application sync issues

View sync status:
```bash
argocd app get my-app
kubectl describe application my-app -n argocd --kubeconfig /path/to/kubeconfig
```

### Reset admin password

```bash
kubectl delete secret argocd-initial-admin-secret -n argocd --kubeconfig /path/to/kubeconfig
kubectl rollout restart deployment argocd-server -n argocd --kubeconfig /path/to/kubeconfig
```

## References

- [ArgoCD Documentation](https://argo-cd.readthedocs.io/)
- [ArgoCD Helm Chart](https://github.com/argoproj/argo-helm/tree/main/charts/argo-cd)
- [ArgoCD Getting Started Guide](https://argo-cd.readthedocs.io/en/stable/getting_started/)
- [ArgoCD Best Practices](https://argo-cd.readthedocs.io/en/stable/user-guide/best_practices/)
