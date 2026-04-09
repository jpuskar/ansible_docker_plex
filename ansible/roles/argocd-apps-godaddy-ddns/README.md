# argocd-apps-godaddy-ddns

Deploys a Kubernetes Deployment that updates GoDaddy DNS A-records with the current public IP (dynamic DNS). Runs as a long-lived process with built-in exponential backoff on failure (60s → doubled each failure → max 1h).

## Secret — Manual Creation

The `godaddy-config` secret is **not** managed by this role. Create it manually before deploying:

1. Copy the template from [docs/godaddy-config-secret.yaml](docs/godaddy-config-secret.yaml)
2. Replace the placeholder values:
   - `GODADDY_DOMAIN` → your FQDN (e.g. `home.example.com`)
   - `GODADDY_API_KEY` → API key from https://developer.godaddy.com/keys
   - `GODADDY_API_SECRET` → corresponding API secret
3. Apply to the cluster:
   ```bash
   kubectl apply -f godaddy-config-secret.yaml
   ```

> **Note:** GoDaddy DNS API requires 10+ domains on your account or an active
> "Premium Discount Domain Club" plan. See [upstream issue #21](https://github.com/CarlEdman/godaddy-ddns/issues/21).

## What the Role Deploys

| Resource | Template |
|----------|----------|
| Namespace (`godaddy-ddns`) | `templates/namespace.yaml` |
| Deployment (`godaddy-ddns`) | `templates/godaddy-ddns-deployment.yaml` |

The container checks the public IP every `GODADDY_INTERVAL` seconds (default 300 / 5 min) and updates GoDaddy only when the IP has changed. On API failure, it backs off exponentially (60s, 120s, 240s, ... up to 1h) then resets on success.
