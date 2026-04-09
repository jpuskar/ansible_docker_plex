# valheim-server

Rootless Valheim dedicated server Docker image. Runs entirely as UID 10000 — never root.

## What's included
- Valheim dedicated server (Steam AppID 896660) via steamcmd
- [valheim-logfilter](https://github.com/community-valheim-tools/valheim-server-docker/tree/main/valheim-logfilter) for cleaner logs (filters empty lines, UTF-8 garbage, debug spam)
- `tini` as PID 1 for proper signal handling
- Graceful shutdown (SIGINT → server saves world and exits)

## What's NOT included
- No auto-update (handle via image rebuild / rolling restart)
- No built-in backup rotation (use K8s CronJob or external tooling)
- No supervisord / cron / syslog / status httpd
- No RCON — Valheim doesn't support it. The server auto-saves every ~20 minutes and on shutdown.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SERVER_NAME` | `My Server` | Server name shown in browser |
| `WORLD_NAME` | `Dedicated` | World name (filename without extension) |
| `SERVER_PORT` | `2456` | UDP port |
| `SERVER_PASS` | *(empty)* | Server password (min 5 chars if set) |
| `SERVER_PUBLIC` | `false` | Show in server browser (`true`/`false`) |
| `SERVER_ARGS` | *(empty)* | Additional CLI args |
| `CROSSPLAY` | `false` | Enable crossplay (opens port +2) |
| `ADMINLIST_IDS` | *(empty)* | Space-separated SteamID64s for admin |
| `BANNEDLIST_IDS` | *(empty)* | Space-separated SteamID64s to ban |
| `PERMITTEDLIST_IDS` | *(empty)* | Space-separated SteamID64s to allowlist |

## Volumes

| Path | Purpose |
|---|---|
| `/config` | World saves, backups, admin lists |
| `/server` | Server binary (cache to skip ~1GB download on restart) |

## Ports

| Port | Protocol | Purpose |
|---|---|---|
| 2456 | UDP | Game traffic |
| 2457 | UDP | Steam query |

## Build

```bash
docker build -t valheim-server .
```

## Notes
- The server auto-saves every ~20 minutes and always saves on graceful shutdown (SIGINT/SIGTERM).
- UID/GID is 10000:10000. Ensure mounted volumes are owned accordingly.
- To update the server binary, rebuild the image or delete the `/server` volume contents and restart.
