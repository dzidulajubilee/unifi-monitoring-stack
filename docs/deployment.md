# Deployment

## First install

```bash
git clone https://github.com/dzidulajubilee/unifi-monitoring-stack.git
cd unifi-monitoring-stack
./install.sh
```

The installer checks Docker and openssl, asks for the time zone, the UniFi controller and account (or API key),
the Grafana admin login, whether to run Dozzle (and its login) and the Grafana address report PDFs link to
(optionally the email server too). It saves the UniFi console's certificate, makes nginx's HTTPS certificate,
writes every `.env` file and runs `docker compose up -d --build`. Run it again
to change any of those settings: it offers the current values, and settings it doesn't ask about are left as they
are. `--no-start` saves the settings without starting anything.

It also writes a `.env` next to `compose.yaml` with the `UID` and `GID` the containers run as: on a new install,
the user running it (the one behind `sudo`); later, whoever the data already belongs to. Compose reads only that
file for the `user:` lines in `compose.yaml`; the `UID`/`GID` lines in the service `.env` files don't reach it.
Without it the containers run as 1000:1000.

The admin login in `grafana/.env` only takes effect the first time Grafana starts. After that, the installer
can reset the admin password for you (it runs `grafana cli admin reset-admin-password --password-from-stdin`, so
the password never appears in the process list).

To set up by hand instead, copy each `*/.env.example` to `.env` and edit it, write the root `.env` with
`printf 'UID=%s\nGID=%s\n' "$(id -u)" "$(id -g)" > .env`, make the HTTPS certificate (see [HTTPS](#https)) and run
`docker compose up -d`. At minimum set the UniFi controller and account
(or API key) in `unpoller/.env`, a strong Grafana admin password in `grafana/.env`, and `TZ` everywhere.

The data folders (`grafana/data`, `prometheus/data`, `loki/data`, `reporter/out`) must be writable by the UID/GID
the containers run as.

## HTTPS

nginx serves Grafana, the reports portal and Dozzle over HTTPS on their usual ports (3000, 8088, 8080); the
containers themselves publish nothing. `http://` on those ports redirects to `https://`. The configuration is
`nginx/default.conf`.

`./install.sh` makes a self-signed certificate for the server's IP address and host name in `nginx/certs`
(`server.crt` and `server.key`, ignored by git) the first time. Browsers warn about it until it's accepted or
trusted. To use your own certificate, from your company's certificate authority or a public one, copy it (with
any intermediate certificates appended) to `nginx/certs/server.crt`, its key to `nginx/certs/server.key`, and run
`docker compose restart nginx`. Delete both files and run `./install.sh` to go back to a self-signed one.

When you change the address people use, update the Grafana address under the portal's **Settings** too, so links
in report PDFs use it.

## UniFi console certificate

UniFi consoles use a self-signed certificate, which normal checks reject, so most setups turn checking off and
would trust anyone posing as the console. Instead, `./install.sh` saves the console's certificate to
`unpoller/certs/controller.pem` after showing its SHA-256 fingerprint, and UnPoller (through
`UP_UNIFI_CONTROLLER_0_SSL_CERT_PATH_0`) and the installer accept only that certificate.

Check the fingerprint before trusting it: open the console in a browser and view the certificate, or on the console
over SSH run `openssl x509 -noout -fingerprint -sha256 -in /data/unifi-core/config/unifi-core.crt`.

If the console gets a new certificate (a factory reset, some upgrades, or one you installed), UnPoller logs
`certificate signature does not match` and stops collecting. Run `./install.sh --cert`: it shows the new
fingerprint, saves it once you confirm and restarts UnPoller.

To pin by hand:

```bash
openssl s_client -connect <console IP>:443 </dev/null 2>/dev/null | openssl x509 > unpoller/certs/controller.pem
openssl x509 -in unpoller/certs/controller.pem -noout -fingerprint -sha256   # compare with the console
echo 'UP_UNIFI_CONTROLLER_0_SSL_CERT_PATH_0=/etc/unpoller/certs/controller.pem' >> unpoller/.env
docker compose up -d --force-recreate unpoller
```

## Dozzle

Dozzle shows every container's logs in the browser. It holds the Docker socket, which gives control of the server,
so it only runs when turned on: `COMPOSE_PROFILES=dozzle` in the `.env` next to `compose.yaml`. `./install.sh`
asks, and sets or removes that line. To turn it off by hand, remove the line and run
`docker compose --profile dozzle rm -sf dozzle`.

Dozzle asks for a username and password (`DOZZLE_AUTH_PROVIDER=simple` in `dozzle/.env`). The accounts live in
`dozzle/data/users.yml`, which git ignores; it holds a bcrypt hash of each password, never the password itself.

`./install.sh` creates it and can replace it on a later run. To do it by hand, generate the file again and restart
Dozzle (this puts the password on the command line, so run it where nobody else is logged in):

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

**Updating from before HTTPS (v1.1.0 or older):** run `./install.sh` once after `git pull`. It makes the HTTPS
certificate nginx needs, saves the UniFi certificate and asks about Dozzle; press Enter to keep everything else.
Without it nginx doesn't start, and Grafana and the portal aren't reachable. Addresses change from `http://` to
`https://`.

The reporter image is published to `ghcr.io/dzidulajubilee/unifi-monitoring-stack/reporter` after CI passes on
`main` (tags: `latest`, the short commit, and the version for `v*` tags). The repository is private, so the image
is too: a server pulling it signs in once with `docker login ghcr.io` using a GitHub token that can read packages.

To stay on a release instead of following `main`, check out its tag (`git checkout v1.1.0`) and set the reporter
image to that version in `compose.yaml` (`.../reporter:1.1.0`). [CHANGELOG.md](../CHANGELOG.md) lists what
each release changed.

## Ports

| Port | Service |
|---|---|
| 3000 | Grafana, HTTPS through nginx |
| 8088 | Reports portal, HTTPS through nginx |
| 8080 | Dozzle (when turned on), HTTPS through nginx |
| 9090 | Prometheus, on `127.0.0.1` only |

Prometheus and UnPoller have no login, so neither is open to the network. Prometheus listens only on the server
itself; to use its UI from another machine, tunnel to it with `ssh -L 9090:localhost:9090 you@server` and open
<http://localhost:9090>. Grafana's Explore view runs the same queries behind a Grafana login. UnPoller and Loki
have no published port: Prometheus, Grafana and the reporter reach them over the Docker network.
To make Prometheus or Loki reachable from the network, put a proxy with a password and HTTPS in front; see
[Exposing Prometheus or Loki](../README.md#exposing-prometheus-or-loki-on-the-network) in the README.

Grafana, the portal and Dozzle are HTTPS (see [HTTPS](#https)). Keep the stack on a network only trusted people
reach, such as a management VLAN, and don't expose it to the internet.

## Backups

Back up the `.env` files (including the one next to `compose.yaml`), the four data folders, `dozzle/data/users.yml`,
`nginx/certs` (it holds the HTTPS private key) and `unpoller/certs`. `reporter/out/settings.json` holds the email settings,
including the SMTP password.
