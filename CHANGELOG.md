# Changelog

Releases are tagged `vX.Y.Z` on GitHub. Changes on `main` that aren't in a release yet go under *Unreleased*.

## Unreleased

- **Reports, fixed**: an access point renamed during the period (adopted with its model name, then named for its
  room) showed twice, under the old and the new name. Access points are now one row per device under the latest
  name, with the figures from before the rename included; the same for channel changes by access point.
- **Reports, page layout**: every report's pages are checked after layout (mostly empty pages, a heading left
  without its content, anything wider than the page or past its bottom). If something is found, the report is
  laid out again with looser page breaks and the better version is kept; anything left shows as a notification
  and as *pages need a look* in the portal. Long tables (internet line events, address pools) now continue onto
  the next page instead of leaving a gap. Tests render the real report from made-up data (empty, typical and
  very large; weekly, monthly and quarterly) and fail on any layout problem.
- **Security**: nginx updated from 1.28 to 1.31.9, the release that fixes the NGINX2Root vulnerabilities (affected:
  1.9.5 to 1.31.8). The stack doesn't turn on HTTP/2, so the most severe one didn't apply, but the other four
  may. Update with `docker compose pull nginx && docker compose up -d nginx`.
- `REPORTER_IMAGE` in the root `.env` picks the reporter image (a release, or your fork's); the default is unchanged.

## v2.1.1 (2026-11-16)

- **CI**: only the newest 3 reporter images are kept in the registry (the current one and 2 before it); older ones
  are deleted after each publish. Images are published without attestation manifests, so each is one entry.
  Older images, 2.1.0 included, were removed; 2.1.1 has the same code as 2.1.0.

## v2.1.0 (2026-11-16)

No manual steps: `git pull`, `docker compose pull reporter`, `docker compose up -d`. To get blocks of internet
traffic back on UniFi Network 11, run `./install.sh` and turn on the firewall log over syslog
(docs/deployment.md).

- **Firewall log over syslog** (optional): UniFi Network 11 stopped putting blocks of internet traffic in its System
  Log, so the Firewall dashboard's internet panels and the reports' internet blocks went empty from 10 Nov. A
  syslog receiver (Grafana Alloy, compose profile `syslog`, UDP port 5514) takes UniFi's firewall log into Loki.
  It keeps only messages from the UniFi console's address and only firewall entries, caps the rate, and runs
  read-only without privileges. `./install.sh` asks whether to turn it on; docs/deployment.md has the UniFi side.
  Dashboards and reports use it from the moment it arrives and the System Log before that, so history is kept and
  nothing is counted twice. New: *Ports targeted from the internet* on the Firewall dashboard and *Ports the
  internet tried most* in the report.
- The page-flow check no longer warns about a short heading block that fits whole at the bottom of a page.
- **Reports, Applications**: traffic per day by category, as on the Applications dashboard: a stacked bar per day
  (per week for a quarter) and a table with each category's traffic, share, busiest day and the device that used
  most of it, plus the change per day against last week once there's enough data. Days when UniFi restarted its
  counting are marked and left out of the comparison. *Needs attention* names a category that spiked on one day
  or doubled, with the device behind it.
- **Reports, IP address conflicts**: one row per address with its network, how often, when last, and the devices
  UniFi saw with that address around each conflict (UniFi's entry doesn't name them). *Needs attention* names
  them too.
- **Reports, page flow**: headings stay with their table (the IP address conflicts heading could end a page),
  and every report is checked after layout: a mostly empty page or a heading left at the bottom of a page is logged
  as a warning.
- **UniFi Switches dashboard**: the *Type* filter starts on Switches only; pick Gateway or Access points to add them.
- **Reports, fixed**: when the summary and *Needs attention* didn't fit under the title, the full report left
  page 1 blank and started on page 2. The summary now flows on to the next page instead.
- **Reports**: peaks, averages and data downloaded are compared with last week (month, quarter) as long as it has
  data for 70% of the time; before, a single missing day hid every comparison, such as *Peak vs last week* in
  Devices by network. A note says when the earlier period was incomplete. Counts from the logs and *First seen*
  still need the whole period.
- *Needs attention* puts access point restarts on one line, most restarts first, instead of a line per access point.
- **Firewall dashboard**: *Policies disabled in this time range* is now a table instead of a timeline: each
  policy that was off with its zones by name, whether it is still disabled, enabled again or removed, how long it
  was off and when it was first disabled. The timeline showed zone IDs, and policies disabled for a short time
  were too thin to see.

## v2.0.0 (2026-11-14)

Security fixes after a review of the project. **Updating: run `./install.sh` once after `git pull`**; addresses
change from `http://` to `https://`.

- **HTTPS**: nginx serves Grafana, the reports portal and Dozzle over HTTPS on their usual ports, with a
  self-signed certificate the installer makes (or your own in `nginx/certs`). The containers no longer publish
  ports themselves.
- **UniFi certificate**: the installer saves the UniFi console's certificate after showing its fingerprint, and
  UnPoller and the installer accept only that one, so nobody posing as the console gets the API key or password.
  `./install.sh --cert` saves it again after the console gets a new one.
- **Report email**: only Grafana admins can email a report from the portal. Before, any signed-in user, including
  Grafana viewers, could send one to any address.
- **Fixed**: the email server's certificate wasn't checked. It is now, with a Settings option to skip it for an
  internal server with a self-signed certificate.
- **Dozzle** is off unless turned on (the installer asks; `COMPOSE_PROFILES=dozzle`), since it holds the Docker
  socket. Existing installs with a Dozzle login keep it on.
- The installer no longer puts passwords on the command line (the Dozzle login and the Grafana password reset).
- The installer keeps the containers running as the user their data belongs to, instead of switching to whoever
  runs it with `sudo`.
- **Installer**: firewall zone names are now read from UniFi and written to
  `prometheus/config/rules/zone-names.yml`, instead of being typed in by hand. `./install.sh --zones` refreshes
  them and reloads Prometheus: run it after a UniFi upgrade that renumbers the zones (Network 11 does) or when
  you add a zone.
  It reloads Prometheus from inside the container. Reloading with `docker compose kill -s HUP` made Docker
  treat Prometheus as stopped by hand, so it didn't start again after the server rebooted.
- **UniFi Switches dashboard**: a *Type* filter (Switches, Gateway, Access points) picks which devices it shows.
  It starts on switches and the gateway, so access points with a wired port no longer appear unless you pick them.

## v1.1.0 (2026-11-09)

- **Fixed**: a report made by hand before the send hour stopped that period's scheduled email, and deleting the
  latest report made the schedule make and email it again. The schedule now keeps its own record of what it
  has sent (`out/scheduled.json`).
- Remaking a report for the same period still replaces it; the list of saved reports now says who remade it.
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

## v1.0.0 (2026-11-07)

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
