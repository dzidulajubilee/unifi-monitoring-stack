# Deployment

## First install

```bash
git clone https://github.com/dzidulajubilee/unifi-monitoring-stack.git
cd unifi-monitoring-stack
for f in */.env.example; do cp -n "$f" "${f%.example}"; done   # then edit each .env
docker run --rm amir20/dozzle:v11.4.1 generate admin --password 'choose-a-strong-password' > dozzle/data/users.yml
docker compose up -d
```

The real settings live in the `.env` files, which git ignores. At minimum set the UniFi controller and account
(or API key) in `unpoller/.env`, a strong Grafana admin password in `grafana/.env`, and `TZ` everywhere.
`reporter/.env` can set `GRAFANA_PUBLIC_URL` so report PDFs link into Grafana. The Dozzle line creates its
login (see [Dozzle login](#dozzle-login)).

The data folders (`grafana/data`, `prometheus/data`, `loki/data`, `reporter/out`) must be writable by the UID/GID
the containers run as (1000 by default).

## Dozzle login

Dozzle asks for a username and password (`DOZZLE_AUTH_PROVIDER=simple` in `dozzle/.env`). The accounts live in
`dozzle/data/users.yml`, which git ignores; it holds a one-way hash of each password, never the password itself.

Create it, or change the username or password, by generating the file again and restarting Dozzle:

```bash
docker run --rm amir20/dozzle:v11.4.1 generate admin --password 'choose-a-strong-password' > dozzle/data/users.yml
docker compose restart dozzle
```

Generating replaces the file, so to keep several accounts, generate each one and combine their entries under the
single `users:` key. Anyone signed in has to sign in again after a change. Sign-ins last 7 days
(`DOZZLE_AUTH_TTL`). To turn the login off, set `DOZZLE_AUTH_PROVIDER=none`.

## Updating

```bash
git pull
docker compose pull reporter      # the reporter image GitHub Actions built for this commit
docker compose up -d
```

`docker compose up -d --build` still works and builds the reporter on the server instead of pulling it.
Pulls never touch the `.env` files or the data folders. Grafana reloads changed dashboards by itself.

The reporter image is published to `ghcr.io/dzidulajubilee/unifi-monitoring-stack/reporter` after CI passes on
`main` (tags: `latest`, the short commit, and the version for `v*` tags). The repository is private, so the image
is too: a server pulling it signs in once with `docker login ghcr.io` using a GitHub token that can read packages.

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

Back up the `.env` files, the four data folders and `dozzle/data/users.yml`. `reporter/out/settings.json` holds the email settings,
including the SMTP password.
