# UniFi Monitoring Stack

Docker Compose stack for monitoring UniFi networks: UnPoller collects metrics and the UniFi System Log (plus, optionally, UniFi's firewall log over syslog), Prometheus and Loki store them, Grafana shows them, and a small reporter turns them into weekly, monthly and quarterly PDF reports with a web portal to make, download and email them.

Based on [Techno Tim's unpoller-unifi](https://github.com/timothystewart6/unpoller-unifi). See [Credits](#credits).

## What's included

| Service | What it does | Address |
| ------- | ------------ | ------- |
| UnPoller | Polls the UniFi controller for metrics and the System Log | internal only |
| Prometheus | Stores metrics, kept for 400 days | <http://localhost:9090>, on the server only |
| Loki | Stores UniFi security, firewall and audit logs, kept for 400 days | internal only |
| Grafana | Dashboards for gateway, switches, access points, clients, DPI, sites, PDU and firewall | `https://<server>:3000` |
| Reporter | PDF reports and the reports portal | `https://<server>:8088` |
| nginx | HTTPS for Grafana, the portal and Dozzle | ports 3000, 8088, 8080 |
| Dozzle | Container log viewer, optional and off unless you turn it on | `https://<server>:8080` |
| Syslog receiver | Optional: UniFi's firewall log over syslog into Loki (Grafana Alloy). Keeps only what the UniFi console sends | UDP port 5514 |

## Quick start

You need Docker with the Compose plugin.

```bash
git clone https://github.com/dzidulajubilee/unifi-monitoring-stack.git
cd unifi-monitoring-stack
./install.sh
```

The installer asks for your time zone, UniFi controller and account (or API key), the Grafana login, whether
to run Dozzle, whether to receive UniFi's firewall log over syslog, and the Grafana address for report links. It writes every `.env` file (git ignores them), saves
the UniFi console's certificate so only that console is trusted, makes a self-signed HTTPS certificate and starts
the stack. Press Enter to take a suggested value; blank passwords are generated and shown once at the end. Email
for reports can be set up there too, or later in the portal.

Grafana, the portal and Dozzle are served over HTTPS by nginx. Browsers warn about the self-signed certificate the
first time; accept it, or put your own certificate in `nginx/certs` ([details](docs/deployment.md#https)).

Run `./install.sh` again any time to change these settings: it offers your current values, so Enter keeps them.
`./install.sh --no-start` writes the settings without starting anything, `./install.sh --zones` only
refreshes the firewall zone names from UniFi, and `./install.sh --cert` only saves the UniFi console's
certificate again.

<details>
<summary>Manual setup, without the installer</summary>

1. **Create your settings files.** Each service has a `.env.example`; copy them to `.env`:

   ```bash
   for f in */.env.example; do cp -n "$f" "${f%.example}"; done
   printf 'UID=%s\nGID=%s\n' "$(id -u)" "$(id -g)" > .env   # the user the containers run as
   ```

2. **Edit `unpoller/.env`** with your controller. A dedicated local UniFi account with read-only access is recommended:

   ```env
   UP_UNIFI_CONTROLLER_0_URL=https://192.168.1.1
   UP_UNIFI_CONTROLLER_0_USER=unpoller
   UP_UNIFI_CONTROLLER_0_PASS=your-password
   UP_UNIFI_CONTROLLER_0_SITE=default
   ```

   For the firewall zone panels, also set `UP_UNIFI_CONTROLLER_0_API_KEY` (UniFi Network → Settings → Control Plane → Integrations).

3. **Edit `grafana/.env`**: set `GF_SECURITY_ADMIN_USER` and a strong `GF_SECURITY_ADMIN_PASSWORD`.

4. **Make the HTTPS certificate** for nginx (or copy your own to these two files):

   ```bash
   openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes -subj "/CN=$(hostname)" \
     -addext "subjectAltName=IP:<server IP>,DNS:$(hostname)" -keyout nginx/certs/server.key -out nginx/certs/server.crt
   ```

   Optional, and recommended: pin the UniFi console's certificate as in
   [docs/deployment.md](docs/deployment.md#unifi-console-certificate).

   Dozzle is optional. To run it, add `COMPOSE_PROFILES=dozzle` to `.env` and create its login as in
   [docs/deployment.md](docs/deployment.md#dozzle).

   The syslog receiver is optional too (UniFi Network 11 needs it for blocks of internet traffic). Set the console's
   address in `syslog/.env`, add `syslog` to `COMPOSE_PROFILES` (e.g. `COMPOSE_PROFILES=dozzle,syslog`) and set up
   UniFi as in [docs/deployment.md](docs/deployment.md#firewall-log-over-syslog).

5. **Set your time zone** (`TZ=`) in each `.env`, e.g. `TZ=Europe/London`.

6. **Start the stack**

   ```bash
   docker compose up -d --build
   ```

</details>

## Reports

Open `https://<server>:8088` and sign in with a Grafana account. Make a report for any week, month, quarter or
dates, download it, or let the schedule email it. Grafana admins set the name, time zone, what each version
contains, recipients, schedule and email server under **Settings**. See [docs/reports.md](docs/reports.md).

## Project layout

| Path | What's there |
|---|---|
| `compose.yaml` | The stack |
| `install.sh` | Asks for your settings, writes the `.env` files and starts the stack |
| `*/.env.example` | Settings for each service; the installer copies them to `.env` |
| `reporter/` | Report generator and portal ([docs/reports.md](docs/reports.md), [docs/development.md](docs/development.md)) |
| `grafana/build/` | Dashboard generators ([README](grafana/build/README.md)) |
| `grafana/provisioning/` | Dashboards and data sources Grafana loads |
| `prometheus/`, `loki/` | Their configuration; data folders are created on first start |
| `syslog/` | The optional syslog receiver's configuration (`config.alloy`) |
| `docs/` | [Deployment](docs/deployment.md), [reports](docs/reports.md), [how the reporter works](docs/how-reports-work.md), [data notes](docs/data-notes.md), [development](docs/development.md) |

## Updating

```bash
git pull
docker compose up -d --build
```

Your `.env` files and `reporter/out/` (reports and portal settings) aren't touched by a pull. More in
[docs/deployment.md](docs/deployment.md).

## Exposing Prometheus or Loki on the network

Out of the box neither is reachable from other machines, and that's deliberate: **neither has a login**.
Prometheus listens only on the server (`127.0.0.1:9090`), and Loki and UnPoller aren't published at all. Anyone
who can reach them can read everything they hold: client names, MAC and IP addresses, traffic per device, firewall
and security logs. Loki also accepts writes, so an open Loki lets anyone add fake entries to your security
dashboards and reports.

So **don't just publish the port** (for example by changing `127.0.0.1:9090:9090` back to `9090:9090`, or adding
`3100:3100` to Loki). If you need them from another machine:

- **Only you, now and then:** use an SSH tunnel and change nothing:
  `ssh -L 9090:localhost:9090 you@server`, then open <http://localhost:9090>. For queries, Grafana's Explore
  view already sits behind a Grafana login.
- **Another tool or person on the network** (a second Grafana, a remote Prometheus, a log shipper): put a reverse
  proxy in front that asks for a **password** and serves **HTTPS**, so the password isn't sent in plain text.
  Grafana and the reporter keep talking to Prometheus and Loki directly over the Docker network, so nothing
  inside the stack changes.

Example with [Caddy](https://caddyserver.com/). First make a password hash:

```bash
docker run --rm caddy:2.10.2 caddy hash-password --plaintext 'choose-a-strong-password'
```

Then create `caddy/Caddyfile`, replacing the address with your server's and pasting the hash. Keep only the
blocks you need:

```caddyfile
# Prometheus: https://192.168.1.10:9443
https://192.168.1.10:9443 {
	tls internal
	basic_auth {
		admin <paste-the-hash-here>
	}
	reverse_proxy prometheus:9090
}

# Loki: https://192.168.1.10:3443
https://192.168.1.10:3443 {
	tls internal
	basic_auth {
		admin <paste-the-hash-here>
	}
	reverse_proxy loki:3100
}
```

Then add the proxy to `compose.yaml` and run `docker compose up -d`:

```yaml
  caddy:
    image: caddy:2.10.2
    restart: unless-stopped
    container_name: caddy
    ports:
      - '9443:9443'   # Prometheus
      - '3443:3443'   # Loki
    volumes:
      - ./caddy/Caddyfile:/etc/caddy/Caddyfile:ro
      - ./caddy/data:/data
```

`tls internal` makes Caddy sign its own certificate. Browsers warn about it until you trust Caddy's root
certificate (`caddy/data/caddy/pki/authorities/local/root.crt`). Other tools need that certificate too, or
their "skip TLS verification" option. With a real domain name, drop `tls internal` and Caddy gets a trusted
certificate by itself. Back up `caddy/Caddyfile` and never commit it with a real hash.

Two things that look like a password option but aren't enough:

- **Prometheus's own basic auth** (`--web.config.file`): it works for outside visitors, but then everything inside
  the stack has to send the password as well. The reporter can't do that yet, so reports would stop working.
  Use the proxy instead.
- **Loki's `auth_enabled: true`**: this turns on multi-tenancy (an `X-Scope-OrgID` header), not a login. Anyone
  can still send that header.

Docker publishes ports past host firewalls such as `ufw`, so don't rely on a firewall rule to protect a published
port.

## Troubleshooting

- Logs: `docker compose logs <service>`, or Dozzle at `https://<server>:8080` if you turned it on.
- UnPoller logs `certificate signature does not match`: the UniFi console has a new certificate (after a reset,
  some upgrades, or one you installed). Check the fingerprint the installer shows and run `./install.sh --cert`.
- Check the UniFi controller is reachable from the Docker host, and that the UnPoller account has read access.
- The Firewall dashboard shows zone IDs instead of names, usually after a UniFi upgrade or a new zone: run
  `./install.sh --zones`. It reads the zones from UniFi again and reloads Prometheus.
- The Firewall dashboard's internet panels are empty on UniFi Network 11: it no longer puts those blocks in the
  System Log. Turn on the syslog receiver (`./install.sh`) and point UniFi at it
  ([docs/deployment.md](docs/deployment.md#firewall-log-over-syslog)). Nothing arrives: see the checks there.
- HTTP 429 errors from UniFi: raise `scrape_interval` for `unpoller` in `prometheus/config/prometheus.yml`.
- A report says metrics are missing for part of the period: UnPoller or Prometheus wasn't running then. Totals are estimated from the data that exists.

## Credits

This project started from **[unpoller-unifi](https://github.com/timothystewart6/unpoller-unifi) by Techno Tim** ([Timothy Stewart](https://github.com/timothystewart6)), which provided the original Compose stack and Grafana dashboards. Its commits are kept in this repository's history under their original author. See his [guide](https://technotim.com/posts/unpoller-unifi-metrics/) and [video](https://www.youtube.com/watch?v=cVCPKTHEnI8).

Built on these open-source projects:

- [UnPoller](https://github.com/unpoller/unpoller): polls UniFi controllers
- [Prometheus](https://github.com/prometheus/prometheus): metrics storage
- [Grafana Loki](https://github.com/grafana/loki): log storage
- [Grafana](https://github.com/grafana/grafana): dashboards
- [WeasyPrint](https://github.com/Kozea/WeasyPrint): PDF rendering
- [Dozzle](https://github.com/amir20/dozzle): log viewer
- [Grafana Alloy](https://github.com/grafana/alloy): syslog receiver
- [IBM Plex](https://github.com/IBM/plex) fonts, under the SIL Open Font License (`reporter/fonts/OFL.txt`)

## License

GPL-3.0 for the work added in this repository; see [LICENSE](LICENSE) and [NOTICE](NOTICE) for what that covers.
