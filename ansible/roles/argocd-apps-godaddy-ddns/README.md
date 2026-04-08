# argocd-apps-godaddy-ddns

Deploys a Kubernetes CronJob that updates GoDaddy DNS A-records with the current public IP (dynamic DNS).

## Secret — Manual Creation

The `godaddy-config` secret is **not** managed by this role. Create it manually before deploying:

1. Copy the template from [docs/godaddy-config-secret.yaml](docs/godaddy-config-secret.yaml)
2. Replace the placeholder values with your actual GoDaddy API credentials:
   - `example.com` → your domain
   - `YOUR_GODADDY_API_KEY` → API key from https://developer.godaddy.com/keys
   - `YOUR_GODADDY_API_SECRET` → corresponding API secret
3. Apply to the cluster:
   ```bash
   kubectl apply -f godaddy-config-secret.yaml
   ```

The config file format consumed by the container is:

```
<domain>
--key
<api_key>
--secret
<api_secret>
```

## What the Role Deploys

| Resource | Template |
|----------|----------|
| Namespace (`godaddy-ddns`) | `templates/namespace.yaml` |
| CronJob (`godaddy-ddns`) | `templates/godaddy-ddns-cronjob.yaml` |

The CronJob runs every 5 minutes (configurable via `godaddy_ddns_schedule`) and mounts the `godaddy-config` secret at `/godaddy-ddns.config/`.
