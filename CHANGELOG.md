# Changelog

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
