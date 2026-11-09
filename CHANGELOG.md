# Changelog

## Unreleased

- *Needs attention* in each version warns only about the sections ticked for that version. A section ticked
  for the full report alone no longer raises anything in the summary.
- **Report look**: Settings → Report look picks the colour of charts and links in the PDFs (Blue, the default,
  Teal, Green, Indigo, Amber or Graphite) and takes an optional logo for the top right of the first page.
- The portal has a favicon.
- **Security**: Prometheus (no login) now listens only on the server itself, and UnPoller's metrics port is no
  longer published. Grafana and the reporter are unaffected. Use an SSH tunnel for the Prometheus UI; see
  docs/deployment.md.
- **Installer**: `./install.sh` asks for the settings once, writes every `.env` file and the Dozzle login, and
  starts the stack. Run it again to change settings; Enter keeps the current values. It also writes a root `.env`
  so the containers run as your user instead of always 1000:1000.
- **DNS filtering** (optional): reports read Pi-hole v6 and Technitium DNS Server for queries per day, the share
  blocked, top domains, blocked domains and devices (named from UniFi), with needs-attention lines for failures,
  block-rate jumps and stale blocklists. Set up under Settings → DNS servers.
- Notifications: **Clear all** in the bell panel (per person, asks on the button before clearing).
- The reporter image is built by GitHub Actions and published to GHCR; servers can pull it instead of building.

## 2026-11-07

First release as **unifi-monitoring-stack**, built on Techno Tim's unpoller-unifi.

- **Reports**: weekly, monthly, quarterly and custom PDF reports in full and summary versions, emailed on a
  schedule; plain-language summary and a needs-attention list that names what to act on; sections for internet
  lines and quality, Wi-Fi experience, applications (DPI), security, maintenance and trends; links into Grafana.
- **Portal** on :8088: sign in with Grafana, make and download reports, notifications, settings for name, time
  zone, contents of each version, recipients, schedule and email. Light and dark mode.
- **Dashboards**: Sites, Gateway, Access Points, Clients, Switches, PDU and Applications rebuilt from generators
  with corrected queries; Firewall dashboard with Loki-based blocks, threats and audit log.
- **Stack**: Loki for UniFi System Log events, 400-day retention for metrics and logs, `.env.example` files
  instead of committed `.env`.
