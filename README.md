# UniFi Monitoring Stack

Docker Compose stack for monitoring UniFi networks: UnPoller collects metrics and the UniFi System Log, Prometheus and Loki store them, Grafana shows them, and a small reporter turns them into weekly, monthly and quarterly PDF reports with a web portal to make, download and email them.

Based on [Techno Tim's unpoller-unifi](https://github.com/timothystewart6/unpoller-unifi). See [Credits](#credits).

## What's included

| Service | What it does | Address |
| ------- | ------------ | ------- |
| UnPoller | Polls the UniFi controller for metrics and the System Log | <http://localhost:9130> |
| Prometheus | Stores metrics, kept for 400 days | <http://localhost:9090> |
| Loki | Stores UniFi security, firewall and audit logs, kept for 400 days | internal only |
| Grafana | Dashboards for gateway, switches, access points, clients, DPI, sites, PDU and firewall | <http://localhost:3000> |
| Reporter | PDF reports and the reports portal | <http://localhost:8088> |
| Dozzle | Container log viewer (sign in with the login from step 5) | <http://localhost:8080> |

## Quick start

1. **Clone**

   ```bash
   git clone https://github.com/dzidulajubilee/unifi-monitoring-stack.git
   cd unifi-monitoring-stack
   ```

2. **Create your settings files.** Each service has a `.env.example`; copy them to `.env` (git ignores `.env`, so your credentials never get committed):

   ```bash
   for f in */.env.example; do cp -n "$f" "${f%.example}"; done
   ```

3. **Edit `unpoller/.env`** with your controller. A dedicated local UniFi account with read-only access is recommended:

   ```env
   UP_UNIFI_CONTROLLER_0_URL=https://192.168.1.1
   UP_UNIFI_CONTROLLER_0_USER=unpoller
   UP_UNIFI_CONTROLLER_0_PASS=your-password
   UP_UNIFI_CONTROLLER_0_SITE=default
   ```

   For the firewall zone panels, also set `UP_UNIFI_CONTROLLER_0_API_KEY` (UniFi Network → Settings → Control Plane → Integrations).

4. **Edit `grafana/.env`**: set `GF_SECURITY_ADMIN_USER` and a strong `GF_SECURITY_ADMIN_PASSWORD`.

5. **Create the Dozzle login.** Dozzle shows every container's logs, so it asks for a username and password.
   Pick your own and create its users file (git ignores it):

   ```bash
   docker run --rm amir20/dozzle:v11.4.1 generate admin --password 'choose-a-strong-password' > dozzle/data/users.yml
   ```

6. **Set your time zone** (`TZ=`) in each `.env`, e.g. `TZ=Europe/London`.

7. **Start the stack**

   ```bash
   docker compose up -d --build
   ```

## Reports

Open <http://localhost:8088> and sign in with a Grafana account. Make a report for any week, month, quarter or
dates, download it, or let the schedule email it. Grafana admins set the name, time zone, what each version
contains, recipients, schedule and email server under **Settings**. See [docs/reports.md](docs/reports.md).

## Project layout

| Path | What's there |
|---|---|
| `compose.yaml` | The stack |
| `*/.env.example` | Settings for each service; copy to `.env` |
| `reporter/` | Report generator and portal ([docs/reports.md](docs/reports.md), [docs/development.md](docs/development.md)) |
| `grafana/build/` | Dashboard generators ([README](grafana/build/README.md)) |
| `grafana/provisioning/` | Dashboards and data sources Grafana loads |
| `prometheus/`, `loki/` | Their configuration; data folders are created on first start |
| `docs/` | [Deployment](docs/deployment.md), [reports](docs/reports.md), [how the reporter works](docs/how-reports-work.md), [data notes](docs/data-notes.md), [development](docs/development.md) |

## Updating

```bash
git pull
docker compose up -d --build
```

Your `.env` files and `reporter/out/` (reports and portal settings) aren't touched by a pull. More in
[docs/deployment.md](docs/deployment.md).

## Troubleshooting

- Logs: `docker compose logs <service>`, or Dozzle at <http://localhost:8080>.
- Check the UniFi controller is reachable from the Docker host, and that the UnPoller account has read access.
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
- [IBM Plex](https://github.com/IBM/plex) fonts, under the SIL Open Font License (`reporter/fonts/OFL.txt`)

## License

GPL-3.0 for the work added in this repository; see [LICENSE](LICENSE) and [NOTICE](NOTICE) for what that covers.
