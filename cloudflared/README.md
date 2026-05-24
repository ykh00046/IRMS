# cloudflared/

Local Cloudflare Tunnel configuration for IRMS external access.

## Files

| File | Tracked? | Purpose |
|------|----------|---------|
| `config.example.yml` | yes | Template — safe to commit |
| `config.yml` | **no (gitignored)** | Real config with your tunnel UUID |
| `<UUID>.json` | **no (gitignored)** | Credentials file (auto-created by `cloudflared tunnel create`) |

## Setup

See [`docs/external-access.md`](../docs/external-access.md) for the full
operator guide (Korean).

Quick start:

```bat
setup_tunnel.bat      :: one-time install + tunnel create + DNS route
copy cloudflared\config.example.yml cloudflared\config.yml
notepad cloudflared\config.yml   :: replace placeholders
cloudflared service install      :: register Windows service
```

Validate with `https://<your-host>/health` returning `{"status":"ok"}`.
