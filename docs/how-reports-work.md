# How the reporter works

This explains what happens between "a report is due" and "a PDF lands in someone's inbox", and the rules behind
every figure, sentence and warning in it. For setting reports up see [reports](reports.md); for UnPoller's data
quirks see [data notes](data-notes.md); for working on the code see [development](development.md).

## The pieces

The `reporter` container runs one Python process (`portal.py`) with three parts:

| Part | What it does |
|---|---|
| **Portal** (Flask, port 8088) | Sign-in, the report archive, making reports on request, notifications, Settings |
| **Worker** | Builds reports one at a time from a queue, then emails them |
| **Scheduler** | Every 5 minutes, puts reports that are due into the same queue |

Reports are built from data the rest of the stack already holds. The reporter never talks to UniFi itself:

```
UniFi console ──UnPoller──► Prometheus (metrics, every minute)
                       └──► Loki (UniFi System Log: firewall, IPS, admin logins, internet line events)
Pi-hole / Technitium (optional) ── their own query history

                 all of these ──► reporter ──► two PDFs ──► email + portal archive
```

Everything the reporter keeps is in `reporter/out/`: the PDFs, `settings.json`, notifications and the portal's
session key.

## Periods

| Type | Covers | Made |
|---|---|---|
| Weekly | Monday 00:00 to Sunday 24:00 | Monday |
| Monthly | A calendar month | The 1st |
| Quarterly | Jan–Mar, Apr–Jun, Jul–Sep, Oct–Dec | 1 Jan, Apr, Jul, Oct |
| Custom | Any dates chosen in the portal or on the command line | When asked; never emailed by the scheduler |

All dates use the time zone set in Settings. Every report is compared with the period just before it of the same
length: last week, last month, last quarter, or the same number of days before a custom range.

**The scheduler** makes a report when all of these are true:

- its type is ticked under Settings → Schedule
- its period has ended and the send hour (07:00 by default) has passed
- its PDF doesn't exist yet, so a report is made once even if the container restarts
- the period ended after the scheduler first started (`out/.scheduler-since`), so a new install doesn't make
  reports for old periods it has little data for

## Building a report, step by step

1. **Read the settings**: name, time zone, which sections each version shows, DNS servers.
2. **Collect the figures twice**: once for the period and once for the period before it. Every query asks
   Prometheus or Loki for one number or list over the whole period. A query that fails leaves its figure blank
   (shown as –) instead of stopping the report, and the PDF ends with a line saying how many failed.
3. **Collect only what's needed**: the sections ticked in either version decide which extra collectors run
   (Wi-Fi, applications, internet lines, maintenance, trends, DNS).
4. **Check how complete the data is** (see the next section).
5. **Write the words**: the summary sentences and the *Needs attention* list are generated from the figures.
6. **Draw the charts** as SVG inside the page, so the PDF needs nothing from the internet.
7. **Render two PDFs** from one HTML template with WeasyPrint: the full report and the summary. Each is written
   under a temporary name and renamed when complete, so the portal never lists a half-written file.
8. **Deliver**: email each version to its own recipients, then post a notification in the portal.

Both versions come from the same data, so their numbers always agree.

## How complete the data is

UnPoller can be stopped, and Prometheus and Loki only hold what they were sent, so every report checks how much
of its period actually has data before trusting the totals.

- **Metrics**: per day, the share of minutes Prometheus received from UnPoller.
- **Logs**: the share of days that have any UniFi System Log lines in Loki.

These coverage figures drive four rules:

| Rule | Threshold | Effect |
|---|---|---|
| Missing-data note | Metrics missing for more than 2% of the period | A note at the top of the report saying roughly how much is missing; totals such as data downloaded are estimated from the data that exists |
| Comparing with the previous period | The previous period has data for at least 95% of it | Below that, a change reads "no earlier week to compare" rather than showing a misleading jump |
| Trends | A period needs metrics for at least 50% of it (logs for 90% of days) | Periods below that show as a dashed empty bar instead of a misleadingly low value |
| Naming devices in the DNS section | Metrics for at least 95% of the period | See [DNS filtering](#dns-filtering) |

## The two versions

**What to include** under Settings has a list for each version. A section appears in a version only when it's
ticked there. Two things deliberately cover everything ticked in *either* version:

- the summary sentences, so the summary still mentions, say, the internet lines when it leaves out their page
- the *Needs attention* list, so the summary still warns about an access point problem without the Wi-Fi pages

One exception: the sentence about data downloaded follows each version's own *Data downloaded* tick.

The **headline numbers** follow the sections shown: data downloaded (if ticked), peak devices online (Devices
or Access points), blocked from the internet (Security) and threats blocked (Security or Threats). If none
match, all of them are shown, except data downloaded when it's unticked.

## Where the main figures come from

| Figure | Source | How |
|---|---|---|
| Data downloaded / uploaded | Prometheus | UniFi's site-wide internet rate, averaged per minute over the period and multiplied by its length. The gateway's byte counters can't be used (see [data notes](data-notes.md)) |
| Peak devices online | Prometheus | The highest number of connected devices in any 5-minute window |
| Different devices / first seen | Prometheus | Devices seen in the period; "first seen" means not seen in the period before |
| Devices by network | Prometheus | Per network, as in Grafana's Clients dashboard: the most devices connected at once (5-minute steps), the average over the period, and how many different devices used it. Each device counts once per network, even with two addresses; a device that moved between networks counts on each. Wi-Fi devices caught before they got an address are left out. A stacked chart shows devices per network over the period (averaged per hour for a week, 6 hours for a month, a day for longer) as one line per network: the network with the most devices on average gets the report's blue, the others keep a colour by name, and the table beside it is the legend. Networks named under Settings → What to include → *Leave out of Devices by network* are left out of the table only |
| Latency, CPU, memory, temperature | Prometheus | Averages and peaks of the gateway's own readings |
| Internet line availability | Prometheus | UniFi's uptime figure per line; a line that never carried traffic shows as standby |
| Outages, failovers, ISP latency and packet loss | Loki | UniFi's internet line events |
| Firewall blocks | Loki | Firewall log lines, split by whether the source address is public (from the internet) or private (from inside) |
| Threats | Loki | Intrusion prevention events; *blocked* means UniFi blocked it |
| Admin logins | Loki | Admin access events; "from outside" means from a public address |
| Wi-Fi experience | Prometheus | UniFi's satisfaction score for wireless devices, averaged |
| Applications | Prometheus | UniFi's per-application traffic for the last 24 hours, averaged over the period into shares (needs DPI) |
| Trends | Both | The same headline figures for this period and the 7 weeks, 5 months or 3 quarters before it, so 8, 6 or 4 bars |

## Needs attention

Each line names what to act on. A rule only runs when its section is ticked in either version.

| Area | Raised when |
|---|---|
| Gateway | It restarted; memory use climbed more than 5 points over the period; memory peaked above 90% |
| Access points | One restarted; 2.4 GHz channel busy more than 60% of the time; more than 15% of Wi-Fi packets resent; devices averaged a signal below −75 dBm |
| Wi-Fi | Devices with an average signal below −75 dBm (named, worst first); average Wi-Fi experience below 80%; an access point changed channel more than 3 times a day; IP address conflicts |
| Security | Threats detected but not blocked; high-severity threats (with the device most involved); admin logins from outside the network, with the addresses and whether one is your own internet address |
| Applications | Peer-to-peer file sharing above 5% of identified traffic, with the devices responsible |
| Internet lines | A line went down; latency and packet-loss events 3 or more times; speed tests averaging under half the speed the plan promises; a line not active or backup |
| Maintenance | Firmware updates waiting; an address pool 80% full or more; console storage 85% full or more; a fibre module receiving below −20 dBm, its signal falling 2 dB or more over the period, or running at 70 °C or hotter |
| DNS | See below |

## DNS filtering

Optional: switched on under Settings → DNS servers, and shown per server.

**Where the figures come from.** Each server is asked for the report's dates from the history it keeps itself.

- **Pi-hole (v6)**: its long-term database. Each call takes about 10 seconds per month of history, so calls may
  run for up to 5 minutes. Pi-hole keeps 91 days by default (`database.maxDBdays`). A quarterly report needs 185
  to compare with the whole previous quarter; the report says when a server holds less than its period.
- **Technitium**: its dashboard statistics. Technitium answers a date range in hourly steps up to about 6½ days
  and in whole UTC days beyond that, and it includes the step that holds the end time. The reporter therefore
  asks in pieces of at most 6 days, each ending an hour early, and adds them up, which matches Technitium's own
  totals exactly in any time zone. When Technitium blocks by answering "no such name" (NXDOMAIN), blocked
  answers are taken out of the *names that don't exist* share; if the token can't read settings, that row is
  left out.

**The servers' own queries are left out.** A DNS server often queries itself (from 127.0.0.1), for example a
health check asking for the same name every few seconds. Those queries come off the totals, the blocked, cached
and forwarded counts, the device count and list, the chart and the top domains, and the section note says how many
were left out.

- Pi-hole: measured exactly from its query log, including which domains they asked for.
- Technitium has no per-device log, so they're taken as answered from cache, and they come off a domain only
  when that domain was asked for about as often as the server's own queries.

**Naming devices.** DNS servers only see the address a query came from, and DHCP leases move addresses between
devices, so a name is only shown when it can't be someone else's:

| Situation | The list shows |
|---|---|
| One device used the address for the whole period | Its UniFi name and the address |
| Several devices used the address during the period | "More than one device" and the address |
| One device used several addresses, and nobody else used them | One row for the device, counts added, every address listed |
| UniFi doesn't know the address | The DNS server's own name for it, if any, otherwise the address |
| UniFi data covers less than 95% of the period | Addresses only, and the note says why |

The totals, shares and domain lists never depend on names, so they're complete either way. A fixed address in
UniFi for the devices that matter keeps their names right everywhere.

**Comparisons.** A DNS figure is compared with the previous period only when that period's data came back and
the server held all of it. The block-rate change is shown in percentage points.

**DNS needs attention when:**

- a server can't be read (the reason is given; the report is still sent)
- the share blocked rose more than 10 points on the previous period (naming the device asking most)
- more than 2% of lookups failed with a server failure (Technitium)
- Pi-hole's blocklists were last updated more than 14 days ago

## Delivery and notifications

- **Email**: each version goes to its own list for that report type (Settings → Who gets each report). The body
  is the version's summary sentences; the PDF is attached. Without an email server set, reports are only saved.
- **Notifications** (the bell in the portal):
  - a report was made, by whom, and where it was sent
  - some figures couldn't be fetched
  - an email failed: shown to admins and to the person who asked, because it can include addresses and server
    errors
  - settings were changed: shown to admins
- **Failures never lose a report**: if email fails, the PDFs are still in the archive. If the build fails, the
  notification says why and the worker moves on to the next job.

## Sign-in and settings

- **Sign-in** checks the username and password against Grafana. No separate accounts.
- **Settings** is only for Grafana admins (server admin or organisation Admin).
- **Login limits**: five failed sign-ins from one address lock it out for 5 minutes. Sessions last 12 hours, and
  every form carries a token against cross-site requests.
- **`settings.json`** holds the email password and the DNS servers' passwords, so it's readable by the container
  only. Passwords are never shown again in the portal: leaving the field blank keeps the saved one.
