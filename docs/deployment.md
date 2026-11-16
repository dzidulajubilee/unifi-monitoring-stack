# Deployment

## First install

```bash
git clone https://github.com/dzidulajubilee/unifi-monitoring-stack.git
cd unifi-monitoring-stack
./install.sh
```

The installer checks Docker and openssl, asks for the time zone, the UniFi controller and account (or API key),
the Grafana admin login, whether to run Dozzle (and its login), whether to receive UniFi's firewall log over
syslog (and the console's address) and the Grafana address report PDFs link to
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

## Firewall log over syslog

UniFi Network 11 stopped putting blocks of internet traffic in its System Log, the log UnPoller collects. Blocks
of your own devices are still there. The console's gateway still logs every hit of a policy with *Syslog
Logging* on, with the protocol and ports, and sends it to a syslog server. The optional receiver (Grafana Alloy,
`syslog/config.alloy`) takes that on **UDP port 5514** and stores it in Loki. The Firewall dashboard and the
reports use it for blocks of internet traffic from the moment it arrives, and the System Log before that, so
history is kept and nothing is counted twice. It also adds *Ports targeted from the internet*, which the System
Log can't show.

**Turn it on:** run `./install.sh` and answer yes to *Receive UniFi's firewall log over syslog?*. It asks for the
console's address, writes `syslog/.env`, adds `syslog` to `COMPOSE_PROFILES` in the root `.env` and starts it. By
hand: copy `syslog/.env.example` to `syslog/.env`, set `SYSLOG_FROM` (dots escaped, e.g. `192\.168\.1\.1`) and
`SYSLOG_SOURCE` (the controller URL as in `unpoller/.env`), add `syslog` to `COMPOSE_PROFILES`, and run
`docker compose up -d`.

**Then in UniFi:**

1. Settings > CyberSecure > Traffic Logging. Set **Activity Logging (Syslog)** to **SIEM Server**, the server
   address to this server's IP and the port to `5514`.
2. Tick only **Gateway** and **Firewall Default Policy**. Gateway carries the gateway's own firewall log (the
   receiver keeps those lines and drops the rest of the gateway's messages); Firewall Default Policy adds blocks by
   UniFi's built-in policies. The other options, Triggers and Security Detections included, send UniFi's events
   in CEF, a copy of what UnPoller already reads from the System Log (and in Network 11 without blocks of internet
   traffic either); the receiver can't read them and logs `invalid or unsupported framing` for each.
3. Apply. On each firewall policy whose blocks you want to see, turn on **Syslog Logging**.

Don't send UniFi's flow logs of allowed traffic here: that can be hundreds of messages a second.

**What it accepts:** only messages sent from the console's address (`SYSLOG_FROM`). Messages from any other
address are dropped before they reach Loki, and with `SYSLOG_FROM` empty nothing is kept. Only firewall entries
are kept, the rest of what UniFi sends is dropped, and at most 50 messages a second (bursts of 500) get through,
so a flood can't fill the disk. The container runs as the stack's user with a read-only file system, no Linux
capabilities and 256 MB of memory at most, and doesn't send usage statistics to Grafana Labs. Syslog over UDP
has no login or encryption, and a device on the same network could fake the console's address, so keep the
stack on a trusted network (see [Ports](#ports)). If the console sends from more than one address, list them all
in `SYSLOG_FROM`, separated by `|`.

**Nothing arrives?**

- Check UniFi sends to this server's IP and port 5514, and that Gateway is ticked.
  Changes can take a minute to apply.
- `sudo tcpdump -ni any udp port 5514` on the server shows whether messages arrive, and from which address. That
  address has to be in `SYSLOG_FROM`.
- `docker compose logs syslog` shows configuration errors.
- On the dashboard, only blocks are shown, and only from policies with Syslog Logging on.

To turn it off, remove `syslog` from `COMPOSE_PROFILES` (or answer no in `./install.sh`), run
`docker compose --profile syslog rm -sf syslog`, and set Activity Logging back to off in UniFi.

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
`main` (tags: `latest`, the short commit, and the version for `v*` tags). Only the newest 3 images are kept. To use
another image, such as a release or one your own fork builds, set `REPORTER_IMAGE` in the `.env` next to
`compose.yaml`, e.g. `REPORTER_IMAGE=ghcr.io/dzidulajubilee/unifi-monitoring-stack/reporter:2.1.1`. A private
image needs `docker login ghcr.io` once, with a GitHub token that can read packages.

To stay on a release instead of following `main`, check out its tag (`git checkout v2.1.1`) and set
`REPORTER_IMAGE` to that version (`.../reporter:2.1.1`). [CHANGELOG.md](../CHANGELOG.md) lists what
each release changed.

## Ports

| Port | Service |
|---|---|
| 3000 | Grafana, HTTPS through nginx |
| 8088 | Reports portal, HTTPS through nginx |
| 8080 | Dozzle (when turned on), HTTPS through nginx |
| 9090 | Prometheus, on `127.0.0.1` only |
| 5514/udp | Syslog receiver (when turned on); keeps only what the UniFi console sends |

Prometheus and UnPoller have no login, so neither is open to the network. Prometheus listens only on the server
itself; to use its UI from another machine, tunnel to it with `ssh -L 9090:localhost:9090 you@server` and open
<http://localhost:9090>. Grafana's Explore view runs the same queries behind a Grafana login. UnPoller and Loki
have no published port: Prometheus, Grafana and the reporter reach them over the Docker network.
To make Prometheus or Loki reachable from the network, put a proxy with a password and HTTPS in front; see
[Exposing Prometheus or Loki](../README.md#exposing-prometheus-or-loki-on-the-network) in the README.

Grafana, the portal and Dozzle are HTTPS (see [HTTPS](#https)). Keep the stack on a network only trusted people
reach, such as a management VLAN, and don't expose it to the internet.

## Backups

Back up the `.env` files (including the one next to `compose.yaml` and `syslog/.env`), the four data folders, `dozzle/data/users.yml`,
`nginx/certs` (it holds the HTTPS private key) and `unpoller/certs`. `reporter/out/settings.json` holds the email settings,
including the SMTP password.
