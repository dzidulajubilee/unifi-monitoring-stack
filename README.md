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
| Dozzle | Container log viewer | <http://localhost:8080> |

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

5. **Set your time zone** (`TZ=`) in each `.env`, e.g. `TZ=Europe/London`.

6. **Start the stack**

   ```bash
   docker compose up -d --build
   ```

## Reports

Open <http://localhost:8088> and sign in with a Grafana account.

- **Everyone** can make a report for the last week, month or quarter, or any date range, and view or download saved reports.
- **Grafana admins** also get **Settings**: the name shown on the portal and reports, the time zone, which sections reports include, the email server, who receives each report, and the schedule.

Scheduled reports are made after each week, month or quarter closes (07:00 by default) and emailed to the recipients set for them. Reports are saved in `reporter/out/`.

From the command line:

```bash
docker compose exec reporter python report.py run weekly
docker compose exec reporter python report.py run custom --start 2026-11-01 --end 2026-11-07
```

Add `--no-email` to only save the PDF.

For Gmail use `smtp.gmail.com`, port 587, STARTTLS and an [app password](https://myaccount.google.com/apppasswords).

The portal serves plain HTTP. Keep it on your LAN, or put it behind a reverse proxy with HTTPS before exposing it.

## Updating

```bash
git pull
docker compose up -d --build
```

Your `.env` files and `reporter/out/` (reports and portal settings) aren't touched by a pull.

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
