# ArgoCD Applications

This directory contains ArgoCD Application manifests for GitOps-based deployments.

## Philosophy

**Ansible bootstraps, ArgoCD manages.**

- Use Ansible to install ArgoCD and create Application resources
- ArgoCD syncs and manages the actual workloads from Git
- Infrastructure as Code, tracked in Git

## Directory Structure

```
argocd-apps/
├── openebs-rawfile/
│   ├── application.yaml              # ArgoCD Application for Helm chart
│   ├── storageclass-application.yaml # ArgoCD Application for StorageClass
│   ├── storageclass.yaml             # StorageClass manifest
│   └── values.yaml                   # Helm values (for reference)
└── README.md
```

## How It Works

### 1. Helm Chart from Helm Repository

`application.yaml` references the OpenEBS Rawfile Helm chart directly from the Helm repository with inline values:

```yaml
source:
  chart: rawfile-csi
  repoURL: https://openebs.github.io/rawfile-localpv
  targetRevision: 0.9.0
  helm:
    values: |
      # Inline Helm values here
```

### 2. Custom Manifests from Git

`storageclass-application.yaml` references manifests from THIS Git repository:

```yaml
source:
  repoURL: https://github.com/YOUR_ORG/YOUR_REPO.git
  path: argocd-apps/openebs-rawfile
  directory:
    include: 'storageclass.yaml'
```

## Setup

### Prerequisites

1. **ArgoCD installed** (use the `argocd` Ansible role)
2. **This repo committed to Git**
3. **Git repo accessible from cluster**

### Configuration

1. Update the Git repo URL in `storageclass-application.yaml`:

```yaml
source:
  repoURL: https://github.com/yourusername/your-repo.git
```

Or set it when running the playbook:

```bash
ansible-playbook playbooks/deploy-argocd-apps.yml \
  -e "argocd_repo_url=https://github.com/yourusername/your-repo.git"
```

2. **(Optional)** Configure ArgoCD repo credentials if your repo is private:

```bash
argocd repo add https://github.com/yourusername/your-repo.git \
  --username YOUR_USERNAME \
  --password YOUR_TOKEN
```

Or via kubectl:

```bash
kubectl create secret generic repo-credentials \
  -n argocd \
  --from-literal=type=git \
  --from-literal=url=https://github.com/yourusername/your-repo.git \
  --from-literal=username=YOUR_USERNAME \
  --from-literal=password=YOUR_TOKEN

kubectl label secret repo-credentials \
  -n argocd \
  argocd.argoproj.io/secret-type=repository
```

### Deploy Applications

Run the Ansible playbook:

```bash
ansible-playbook playbooks/deploy-argocd-apps.yml
```

This creates the ArgoCD Application resources. ArgoCD then:
1. Syncs the Helm chart from the Helm repository
2. Syncs the StorageClass from your Git repository
3. Deploys everything to the cluster
4. Monitors for drift and auto-syncs changes

## Verify Deployment

### Check Applications

```bash
kubectl get applications -n argocd
```

Expected output:
```
NAME                            SYNC STATUS   HEALTH STATUS
openebs-rawfile                 Synced        Healthy
openebs-rawfile-storageclass    Synced        Healthy
```

### Via ArgoCD UI

```bash
kubectl port-forward svc/argocd-server -n argocd 8080:443
```

Open: https://localhost:8080

### Via ArgoCD CLI

```bash
argocd app list
argocd app get openebs-rawfile
```

## Making Changes

### Update Helm Values

Edit `application.yaml` and commit:

```yaml
helm:
  values: |
    node:
      storage:
        path: /mnt/new-storage-path  # Changed
```

Commit and push. ArgoCD will auto-sync within ~3 minutes (or instantly if you trigger it).

### Update StorageClass

Edit `storageclass.yaml` and commit:

```yaml
parameters:
  fsType: xfs  # Changed from ext4
```

Commit and push. ArgoCD syncs automatically.

### Trigger Manual Sync

```bash
argocd app sync openebs-rawfile
argocd app sync openebs-rawfile-storageclass
```

Or via UI: Click "Sync" button.

## Application Spec Explained

### Auto-sync Policy

```yaml
syncPolicy:
  automated:
    prune: true      # Delete resources not in Git
    selfHeal: true   # Revert manual changes
```

- **prune**: Removes resources deleted from Git
- **selfHeal**: Reverts manual `kubectl` changes back to Git state

### Sync Options

```yaml
syncOptions:
  - CreateNamespace=true  # Auto-create target namespace
```

### Retry Policy

```yaml
retry:
  limit: 5
  backoff:
    duration: 5s
    factor: 2
    maxDuration: 3m
```

Retries failed syncs with exponential backoff.

## Workflow Comparison

### Ansible Approach (Old)

```bash
ansible-playbook -i inventory playbooks/deploy-openebs.yml
```

- Manual execution required
- No drift detection
- Changes not tracked in Git (unless you commit first)

### ArgoCD Approach (New)

```bash
git commit -am "Update storage path"
git push
# ArgoCD syncs automatically
```

- Declarative, Git-driven
- Automatic drift detection and correction
- Full audit trail in Git history
- Rollback = `git revert`

## Adding More Applications

### 1. Create Directory

```bash
mkdir -p argocd-apps/my-new-app
```

### 2. Create Application Manifest

`argocd-apps/my-new-app/application.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-new-app
  namespace: argocd
spec:
  project: default
  source:
    chart: my-chart
    repoURL: https://charts.example.com
    targetRevision: 1.0.0
    helm:
      values: |
        # Your values
  destination:
    server: https://kubernetes.default.svc
    namespace: my-namespace
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

### 3. Update Playbook

Edit `playbooks/deploy-argocd-apps.yml`:

```yaml
applications:
  - name: openebs-rawfile
    manifest: "{{ playbook_dir }}/../argocd-apps/openebs-rawfile/application.yaml"
  - name: my-new-app
    manifest: "{{ playbook_dir }}/../argocd-apps/my-new-app/application.yaml"
```

### 4. Deploy

```bash
ansible-playbook playbooks/deploy-argocd-apps.yml
```

## Troubleshooting

### Application stuck in "OutOfSync"

```bash
argocd app get openebs-rawfile
```

Check the sync status message for errors.

### Application stuck in "Progressing"

```bash
kubectl describe application openebs-rawfile -n argocd
```

Check for resource creation issues.

### Manual sync fails

```bash
argocd app sync openebs-rawfile --dry-run
```

See what changes would be applied.

### Delete and recreate

```bash
kubectl delete application openebs-rawfile -n argocd
kubectl apply -f argocd-apps/openebs-rawfile/application.yaml
```

## Best Practices

1. **Always commit changes to Git first** - ArgoCD syncs from Git, not local files
2. **Use branches for testing** - Create a branch, point Application at it, test, then merge
3. **Enable notifications** - Get alerts when syncs fail
4. **Use Projects** - Organize Applications into ArgoCD Projects for RBAC
5. **Monitor drift** - Set up alerts when manual changes are detected

## Migration Path

### From Ansible to ArgoCD

1. Keep your Ansible role (for air-gapped or emergency deployments)
2. Create ArgoCD Application manifests
3. Deploy via Ansible playbook initially
4. Future changes go through Git + ArgoCD
5. Ansible becomes the bootstrap tool only

### Hybrid Approach

- **Ansible**: Install ArgoCD, create Applications, cluster bootstrapping
- **ArgoCD**: Manage application workloads, day-2 operations

## References

- [ArgoCD Documentation](https://argo-cd.readthedocs.io/)
- [ArgoCD Best Practices](https://argo-cd.readthedocs.io/en/stable/user-guide/best_practices/)
- [App of Apps Pattern](https://argo-cd.readthedocs.io/en/stable/operator-manual/cluster-bootstrapping/)
