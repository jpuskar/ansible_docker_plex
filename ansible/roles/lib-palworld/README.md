# lib-palworld

Library role for deploying a Palworld dedicated server on Kubernetes.

**Do not call this role directly** — use an `argocd-apps-palworld*` role instead.

## What it creates

- Namespace
- ConfigMap (`server-config`) — game server settings fed to `configure.py`
- Secret (`server-password`) — server + admin passwords
- ServiceAccount, Role, RoleBinding — so the pod can read the password secret at startup
- StatefulSet — palworld-server container with PVC for save data
- Service (LoadBalancer, UDP 8211)

## Required variables

| Variable | Description |
|---|---|
| `palworld_instance_name` | Unique name for this server instance |
| `palworld_namespace` | Kubernetes namespace |

## Optional variables

See `defaults/main.yml` for the full list with defaults.
