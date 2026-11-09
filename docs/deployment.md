# Deployment

## First install

```bash
git clone https://github.com/dzidulajubilee/unifi-monitoring-stack.git
cd unifi-monitoring-stack
./install.sh
```

The installer checks Docker, asks for the time zone, the UniFi controller and account (or API key), the Grafana
admin login, the Dozzle login and the Grafana address report PDFs link to (optionally the email server too),
writes every `.env` file, creates `dozzle/data/users.yml` and runs `docker compose up -d --build`. Run it again
to change any of those settings: it offers the current values, and settings it doesn't ask about are left as they
are. `--no-start` saves the settings without starting anything.

It also writes a `.env` next to `compose.yaml` with your `UID` and `GID`. Compose reads only that file for the
`user:` lines in `compose.yaml`; the `UID`/`GID` lines in the service `.env` files don't reach it. Without it the
containers run as 1000:1000.

The admin login in `grafana/.env` only takes effect the first time Grafana starts. After that, the installer
can reset the admin password for you (it runs `grafana cli admin reset-admin-password`).

To set up by hand instead, copy each `*/.env.example` to `.env` and edit it, write the root `.env` with
`printf 'UID=%s\nGID=%s\n' "$(id -u)" "$(id -g)" > .env`, create the Dozzle login (see
[Dozzle login](#dozzle-login)) and run `docker compose up -d`. At minimum set the UniFi controller and account
(or API key) in `unpoller/.env`, a strong Grafana admin password in `grafana/.env`, and `TZ` everywhere.

The data folders (`grafana/data`, `prometheus/data`, `loki/data`, `reporter/out`) must be writable by the UID/GID
the containers run as.

## Dozzle login

Dozzle asks for a username and password (`DOZZLE_AUTH_PROVIDER=simple` in `dozzle/.env`). The accounts live in
`dozzle/data/users.yml`, which git ignores; it holds a one-way hash of each password, never the password itself.

`./install.sh` creates it and can replace it on a later run. To do it by hand, generate the file again and restart Dozzle:

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

To stay on a release instead of following `main`, check out its tag (`git checkout v1.1.0`) and set the reporter
image to that version in `compose.yaml` (`.../reporter:1.1.0`). [CHANGELOG.md](../CHANGELOG.md) lists what
each release changed.

## Ports

| Port | Service |
|---|---|
| 3000 | Grafana |
| 8088 | Reports portal |
| 8080 | Dozzle |
| 9090 | Prometheus, on `127.0.0.1` only |

Prometheus and UnPoller have no login, so neither is open to the network. Prometheus listens only on the server
itself; to use its UI from another machine, tunnel to it with `ssh -L 9090:localhost:9090 you@server` and open
<http://localhost:9090>. Grafana's Explore view runs the same queries behind a Grafana login. UnPoller and Loki
have no published port: Prometheus, Grafana and the reporter reach them over the Docker network.
To make Prometheus or Loki reachable from the network, put a proxy with a password and HTTPS in front; see
[Exposing Prometheus or Loki](../README.md#exposing-prometheus-or-loki-on-the-network) in the README.

Everything is plain HTTP; keep it on the LAN or put a reverse proxy with HTTPS in front before exposing it.

## Backups

Back up the `.env` files (including the one next to `compose.yaml`), the four data folders and `dozzle/data/users.yml`. `reporter/out/settings.json` holds the email settings,
including the SMTP password.
