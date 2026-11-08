"""Settings, paths and the report time zone."""

import json
import logging
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

PROM = os.environ.get("PROM_URL", "http://prometheus:9090")


LOKI = os.environ.get("LOKI_URL", "http://loki:3100")


# Report time zone. Starts from $TZ; the portal's setting overrides it (see load_settings).
TZ = ZoneInfo(os.environ.get("TZ") or "UTC")


OUT = Path(os.environ.get("OUT_DIR", "/out"))
ROOT = Path(__file__).resolve().parent.parent  # reporter/: templates/ and fonts/ live here


log = logging.getLogger("reporter")


# What a report can include; admins switch these on and off in the portal.
SECTIONS = [
    ("summary", "Summary", "A few plain-language sentences and anything that needs attention"),
    (
        "figures",
        "Headline numbers",
        "Data downloaded, peak devices, blocks and threats, with the change from last time",
    ),
    (
        "downloaded",
        "Data downloaded",
        "Data downloaded and uploaded: the headline number, the summary sentence and the Internet and gateway rows",
    ),
    ("gap_note", "Missing-data note", "A note when metrics weren't collected for part of the period"),
    ("throughput", "Internet throughput chart", "Download and upload over the period"),
    (
        "trends",
        "Trends",
        "Key figures for this period next to the ones before it: 8 weeks, 6 months or 4 quarters (not for custom dates)",
    ),
    (
        "internet",
        "Internet and gateway",
        "Data used, latency, failovers, internet lines, gateway CPU, memory and temperature",
    ),
    (
        "isp",
        "Internet lines and quality",
        "Per line: active or backup, availability, outages and their length, ISP latency and packet-loss events, speed tests, data carried",
    ),
    ("devices", "Devices", "Devices online and the ten that used the most data"),
    ("aps", "Access points", "Traffic, clients, Wi-Fi experience and channel use per access point"),
    (
        "apps",
        "Applications",
        "Top applications and categories by traffic (needs DPI on in UniFi and SAVE_DPI=true in unpoller/.env)",
    ),
    (
        "wifi",
        "Wi-Fi experience and stability",
        "Satisfaction, weak-signal devices, 2.4/5 GHz split, disconnections, roaming, channel changes, IP conflicts",
    ),
    (
        "security",
        "Firewall and security",
        "Blocked connections, top blocking rules and sources, threat totals, admin logins",
    ),
    ("threats", "Threats blocked", "Each threat intrusion prevention detected, with device, target and severity"),
    (
        "dns",
        "DNS filtering",
        "Queries, the share blocked, top domains, blocked domains and devices, from the servers under DNS servers",
    ),
    (
        "maintenance",
        "Maintenance",
        "Access point and switch firmware updates, console storage, address pools filling up, fibre module signal",
    ),
    ("events", "Changes and events", "Firewall changes by admins and internet failovers"),
]


STYLES = ("full", "summary")


# what the summary version shows until an admin changes it: page 1 of the full report
SUMMARY_SECTIONS = {"summary", "figures", "throughput", "downloaded", "gap_note"}


SETTINGS = OUT / "settings.json"


def default_settings():
    """First-run values come from the environment; after that settings.json wins."""
    env = os.environ.get
    to = env("REPORT_TO", "")
    return {
        "general": {
            "name": env("REPORT_NAME", ""),
            "timezone": env("TZ") or "UTC",
            "grafana_url": env("GRAFANA_PUBLIC_URL", ""),
            "grafana_links": True,
        },
        "sections": {k: True for k, *_ in SECTIONS},  # full report
        "sections_summary": {k: k in SUMMARY_SECTIONS for k, *_ in SECTIONS},  # summary
        "smtp": {
            "host": env("SMTP_HOST", ""),
            "port": int(env("SMTP_PORT", 587)),
            "security": env("SMTP_SECURITY", "starttls"),
            "user": env("SMTP_USER", ""),
            "password": env("SMTP_PASSWORD", ""),
            "from": env("SMTP_FROM", ""),
        },
        "recipients": {k: to for k in ("weekly", "monthly", "quarterly")},  # full report
        "recipients_summary": {k: "" for k in ("weekly", "monthly", "quarterly")},  # summary only
        "dns": {"servers": []},  # [{kind, name, url, secret, verify}], see dns.py
        "schedule": {
            "enabled": True,
            "hour": int(env("SEND_HOUR", 7)),
            "kinds": env("REPORTS", "weekly,monthly,quarterly").split(","),
        },
    }


def load_settings():
    s = default_settings()
    try:
        saved = json.loads(SETTINGS.read_text())
        for k in s:
            s[k].update(saved.get(k, {}))
    except FileNotFoundError:
        pass
    set_timezone(s["general"]["timezone"])
    return s


def set_timezone(name):
    """Every date in reports and the portal goes through TZ, so switching it here
    is enough. An unknown name leaves the current zone in place."""
    global TZ
    try:
        TZ = ZoneInfo(name)
    except (ValueError, KeyError, OSError):
        log.warning("unknown time zone %r, keeping %s", name, TZ)


def timezones():
    from zoneinfo import available_timezones

    return sorted(
        z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/", "posix/", "right/"))
    ) + ["UTC"]


def display_name(s=None):
    """What the portal calls itself: the admin's chosen name, or Reports."""
    return (s or load_settings())["general"]["name"].strip() or "Reports"


def save_settings(s):
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2))
    os.chmod(tmp, 0o600)  # holds the SMTP password and DNS server passwords
    tmp.replace(SETTINGS)


def split_addresses(text):
    return [a.strip() for a in re.split(r"[,;\s]+", text or "") if a.strip()]
