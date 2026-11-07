# Reports and the portal

The `reporter` service makes PDF reports about the network and serves a small web portal at
`http://<server>:8088`. Sign in with a Grafana account; Grafana admins also see **Settings**.

## What a report contains

Every report is made in two versions from the same data:

- **Full report**: every section ticked for it under Settings → What to include.
- **Summary**: the sections ticked for the summary (by default the first page: summary, needs attention,
  headline numbers, throughput chart).

*Needs attention* and the summary sentences cover everything ticked for either version, and name what to act
on: the device, access point, internet line or address.

| Section | What it shows |
|---|---|
| Summary | Plain-language sentences and *Needs attention* |
| Headline numbers | Data downloaded, peak devices, blocks, threats, with the change from the previous period |
| Internet throughput | Download and upload over the period |
| Trends | Six key figures for this period and the 7 weeks, 5 months or 3 quarters before it |
| Internet and gateway | Data used, latency, gateway CPU, memory, temperature |
| Internet lines and quality | Per line: active or backup, availability, outages, ISP latency and packet-loss events, speed tests |
| Devices | Devices online and the ten that used the most data |
| Access points | Traffic, clients, Wi-Fi experience, channel use per access point |
| Applications | Share of traffic per application and category (needs DPI) |
| Wi-Fi experience and stability | Satisfaction, weak-signal devices, band split, disconnections, roaming, channel changes |
| Firewall and security | Blocks, top rules and sources, threats, admin logins |
| Threats blocked | Each intrusion-prevention event |
| Maintenance | Access point and switch firmware, console storage, address pools, fibre modules |
| Changes and events | Firewall changes by admins |

When a Grafana address is set (Settings → What to include → *Link to Grafana*), section headings, devices and
access points in the PDF link to the matching dashboard, set to the report's dates.

## When reports are made

Weekly reports cover Monday to Sunday, monthly a calendar month, quarterly a calendar quarter, all in the time zone
set in Settings. The scheduler makes each one at the send hour (07:00 by default) after its period closes and
emails each version to its own recipient list. It never back-fills periods that ended before it first started.

Anyone signed in can also make a report for the last or previous week, month or quarter, or for any dates, and
email either version. Reports are kept in `reporter/out/`.

## Command line

```bash
docker compose exec reporter python report.py run weekly                 # last complete week, emailed per Settings
docker compose exec reporter python report.py run monthly --end 2026-10-31 --no-email
docker compose exec reporter python report.py run custom --start 2026-11-01 --end 2026-11-07
```

## Where things are kept

`reporter/out/` holds the reports and the portal's state: `settings.json` (including the SMTP password, readable
by the container only), notifications, and the portal's session key. Back it up with the rest of the stack.

## Things UniFi doesn't tell UnPoller

On UniFi OS consoles UnPoller can't read automatic-backup status, UniFi OS / Network update availability or the
console's own firmware state, so the report leaves them out; check them in UniFi. See
[data notes](data-notes.md) for the other quirks the report works around.
