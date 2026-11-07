# Deployment

## First install

```bash
git clone git@github.com:dzidulajubilee/unifi-monitoring-stack.git
cd unifi-monitoring-stack
for f in */.env.example; do cp -n "$f" "${f%.example}"; done   # then edit each .env
docker compose up -d --build
```

The real settings live in the `.env` files, which git ignores. At minimum set the UniFi controller and account
(or API key) in `unpoller/.env`, a strong Grafana admin password in `grafana/.env`, and `TZ` everywhere.
`reporter/.env` can set `GRAFANA_PUBLIC_URL` so report PDFs link into Grafana.

The data folders (`grafana/data`, `prometheus/data`, `loki/data`, `reporter/out`) must be writable by the UID/GID
the containers run as (1000 by default).

## Updating

```bash
git pull
docker compose up -d --build
```

Pulls never touch the `.env` files or the data folders. Grafana reloads changed dashboards by itself.

## Ports

| Port | Service |
|---|---|
| 3000 | Grafana |
| 8088 | Reports portal |
| 9090 | Prometheus |
| 9130 | UnPoller metrics |
| 8080 | Dozzle |

Loki has no published port. Everything is plain HTTP; keep it on the LAN or put a reverse proxy with HTTPS in
front before exposing it.

## Backups

Back up the `.env` files and the four data folders. `reporter/out/settings.json` holds the email settings,
including the SMTP password.
