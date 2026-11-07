"""Scheduled network reports (weekly, monthly, quarterly) from Prometheus and Loki.

  python report.py run weekly [--end 2026-11-02] [--no-email]   build one report now
  python report.py serve                                         run the schedule forever

Each report covers a closed period (Mon-Sun week, calendar month, calendar
quarter) in the local timezone and compares it with the period before.
Output goes to OUT_DIR as PDF + HTML; the PDF is emailed when SMTP is set.
"""
import argparse
import datetime as dt
import html
import json
import logging
import math
import os
import re
import smtplib
import sys
import threading
import time
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader

PROM = os.environ.get("PROM_URL", "http://prometheus:9090")
LOKI = os.environ.get("LOKI_URL", "http://loki:3100")
# Report time zone. Starts from $TZ; the portal's setting overrides it (see load_settings).
TZ = ZoneInfo(os.environ.get("TZ") or "UTC")
OUT = Path(os.environ.get("OUT_DIR", "/out"))
HERE = Path(__file__).parent
log = logging.getLogger("reporter")

# private address ranges, same as the firewall dashboard
PRIVATE = r"(10|127)\..*|192\.168\..*|172\.(1[6-9]|2[0-9]|3[01])\..*|f[cd][0-9a-f]{2}:.*|fe80:.*"
FW = ('{application="unifi_system_log", category="SECURITY"} |= `"subcategory":"SECURITY_FIREWALL"` '
      '| json sub="subcategory", policy="parameters.TRIGGER.name", sip="parameters.SRC_IP.name", '
      'scl="parameters.SRC_CLIENT.name", sclip="parameters.SRC_CLIENT.ip", dip="parameters.DST_IP.name", '
      'ddev="parameters.DST_DEVICE.name" | sub="SECURITY_FIREWALL" '
      '| label_format src=`{{ or .scl .sip }}`, srcaddr=`{{ or .sclip .sip }}`')
FW_IN = FW + f" | srcaddr !~ `{PRIVATE}`"
FW_OUT = FW + f" | srcaddr =~ `{PRIVATE}`"
IPS = ('{application="unifi_system_log", category="SECURITY"} != `"subcategory":"SECURITY_FIREWALL"` '
       '| json sub="subcategory", evt="event", scl="parameters.SRC_CLIENT.name", sip="parameters.SRC_IP.name", sev="severity" '
       '| sub!="SECURITY_FIREWALL" | label_format src=`{{ or .scl .sip }}`, '
       'type=`{{ regexReplaceAll "_" (regexReplaceAll "^SECURITY_" .sub "") " " }}`')
AUDIT_FW = ('{application="unifi_system_log", category="AUDIT"} |= `FIREWALL` '
            '| json evt="event", section="parameters.SECTION.id" | section=~`FIREWALL_.*|NAT`')
LOGINS = ('{application="unifi_system_log", category="AUDIT"} |= `"event":"ADMIN_ACCESS"` '
          '| json evt="event", ip="parameters.IP.name" | evt="ADMIN_ACCESS"')
WAN_EVENTS = '{application="unifi_system_log", category="INTERNET_AND_WAN"}'


# ---------------------------------------------------------------- periods

KINDS = {"weekly": "week", "monthly": "month", "quarterly": "quarter", "custom": "period"}


def period_end(kind, now):
    """Start of the current (unfinished) period = end of the last closed one."""
    d = now.astimezone(TZ).date()
    if kind == "weekly":
        d -= dt.timedelta(days=d.weekday())
    elif kind == "monthly":
        d = d.replace(day=1)
    else:
        d = d.replace(month=(d.month - 1) // 3 * 3 + 1, day=1)
    return dt.datetime.combine(d, dt.time(), TZ)


def period_start(kind, end, length=None):
    if kind == "custom":
        return end - length
    if kind == "weekly":
        return end - dt.timedelta(days=7)
    months = 1 if kind == "monthly" else 3
    m = end.month - months
    y = end.year + (m - 1) // 12
    return end.replace(year=y, month=(m - 1) % 12 + 1)


def period_label(kind, start, end):
    last = end - dt.timedelta(days=1)
    if kind == "weekly":
        return (f"Week {start.isocalendar().week}",
                f"{start:%-d %b} – {last:%-d %b %Y}")
    if kind == "monthly":
        return f"{start:%B %Y}", f"{start:%-d %b} – {last:%-d %b %Y}"
    if kind == "custom":
        days = max(1, round((end - start).total_seconds() / 86400))
        if start.date() == last.date():
            return f"{start:%-d %b %Y}", "1 day"
        return f"{start:%-d %b} – {last:%-d %b %Y}", f"{days} days"
    return (f"Q{(start.month - 1) // 3 + 1} {start.year}",
            f"{start:%b} – {last:%b %Y}")


# ---------------------------------------------------------------- data sources

def _get(url, params):
    full = url + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(full, timeout=120) as r:
        body = json.load(r)
    if body.get("status") != "success":
        raise RuntimeError(body)
    return body["data"]["result"]


class Source:
    """Runs one period's queries; any failure becomes None, not a crash."""

    def __init__(self, start, end):
        self.start, self.end = start, end
        self.secs = int((end - start).total_seconds())
        self.r = f"{self.secs}s"
        self.errors = []

    def _safe(self, fn, q, *a):
        try:
            return fn(q, *a)
        except Exception as e:  # noqa: BLE001 - one bad query must not kill the report
            self.errors.append(f"{q[:80]}…: {e}")
            log.warning("query failed: %s: %s", q[:120], e)
            return None

    def prom(self, q):
        """Instant query at period end; $R is the period length."""
        return self._safe(lambda q: _get(PROM + "/api/v1/query",
                                         {"query": q.replace("$R", self.r), "time": self.end.timestamp()}), q)

    def prom_range(self, q, step):
        return self._safe(lambda q: _get(PROM + "/api/v1/query_range", {
            "query": q, "start": self.start.timestamp() + step,
            "end": self.end.timestamp(), "step": step}), q)

    def loki(self, q):
        return self._safe(lambda q: _get(LOKI + "/loki/api/v1/query", {
            "query": q.replace("$R", self.r), "time": int(self.end.timestamp() * 1e9)}), q)

    def loki_lines(self, q, limit=500):
        return self._safe(lambda q: _get(LOKI + "/loki/api/v1/query_range", {
            "query": q, "start": int(self.start.timestamp() * 1e9), "end": int(self.end.timestamp() * 1e9),
            "limit": limit, "direction": "forward"}), q)

    # helpers ----------------------------------------------------------
    def scalar(self, q, src="prom"):
        res = (self.prom if src == "prom" else self.loki)(q)
        if res is None:
            return None
        if not res:
            return 0.0 if src == "loki" else None  # loki returns nothing when the count is 0
        v = float(res[0]["value"][1])
        return None if math.isnan(v) else v

    def by(self, q, label, src="prom"):
        res = (self.prom if src == "prom" else self.loki)(q) or []
        return {x["metric"].get(label, ""): float(x["value"][1]) for x in res}

    def coverage(self):
        """Share of the period's days that have data, per source."""
        days = max(1, round(self.secs / 86400))
        def frac(res, per_day=None):
            if not res:
                return 0.0
            vals = [float(v) for _, v in res[0]["values"]]
            if per_day:  # metrics: share of expected samples (Prometheus scrapes UnPoller every 60s)
                return min(1.0, sum(min(v / per_day, 1) for v in vals) / days)
            return sum(1 for v in vals if v > 0) / days
        p = self.prom_range('count_over_time(up{job="unpoller"}[1d])', 86400)
        l = self._safe(lambda q: _get(LOKI + "/loki/api/v1/query_range", {
            "query": q, "start": int((self.start.timestamp() + 86400) * 1e9),
            "end": int(self.end.timestamp() * 1e9), "step": "86400"}),
            'sum(count_over_time({application="unifi_system_log"}[1d]))')
        return {"prom": frac(p, 1440 * 0.98), "loki": frac(l)}


# ---------------------------------------------------------------- collect

def collect(s: Source):
    d = {}
    # internet
    # The UDM's per-port WAN byte counters barely move, so traffic comes from the
    # site-level WAN rate gauge (bytes/s), averaged over the period and scaled up.
    rx = 'max(unpoller_site_receive_rate_bytes{subsystem="wan"})'
    tx = 'max(unpoller_site_transmit_rate_bytes{subsystem="wan"})'
    d["wan_down"] = s.scalar(f"avg_over_time({rx}[$R:1m]) * {s.secs}")
    d["wan_up"] = s.scalar(f"avg_over_time({tx}[$R:1m]) * {s.secs}")
    d["wan_p95"] = s.scalar(f"quantile_over_time(0.95, {rx}[$R:5m]) * 8")
    d["latency_avg"] = s.scalar('avg_over_time(unpoller_site_latency_seconds{subsystem="www"}[$R])')
    d["latency_max"] = s.scalar('max_over_time(unpoller_site_latency_seconds{subsystem="www"}[$R])')
    up = s.by("avg_over_time((unpoller_wan_uptime_percentage >= 0)[$R:5m])", "wan_name")
    names = s.by("max_over_time(unpoller_wan_uptime_percentage[$R])", "wan_name")
    d["wans"] = [{"name": n, "uptime": up.get(n)} for n in sorted(names)]
    d["failovers"] = s.scalar(f'sum(count_over_time({WAN_EVENTS} |= `FAILED_OVER` [$R]))', "loki")

    # gateway
    gw = 'type="udm"'
    d["cpu_avg"] = s.scalar(f"avg(avg_over_time(unpoller_device_cpu_utilization_ratio{{{gw}}}[$R]))")
    d["cpu_max"] = s.scalar(f"max(max_over_time(unpoller_device_cpu_utilization_ratio{{{gw}}}[$R]))")
    d["mem_start"] = s.scalar(f"avg(avg_over_time(unpoller_device_memory_utilization_ratio{{{gw}}}[1d] offset {s.secs - 86400}s))")
    d["mem_end"] = s.scalar(f"avg(avg_over_time(unpoller_device_memory_utilization_ratio{{{gw}}}[1d]))")
    d["mem_max"] = s.scalar(f"max(max_over_time(unpoller_device_memory_utilization_ratio{{{gw}}}[$R]))")
    d["temp_max"] = s.scalar(f'max(max_over_time(unpoller_device_temperature_celsius{{{gw},temp_area="CPU"}}[$R]))')
    d["gw_reboots"] = s.scalar(f"sum(resets(unpoller_device_uptime_seconds{{{gw}}}[$R]))")

    # clients
    clients = "unpoller_client_uptime_seconds"
    d["clients_peak"] = s.scalar(f"max_over_time(count({clients})[$R:5m])")
    d["clients_unique"] = s.scalar(f"count(count by (mac) (last_over_time({clients}[$R])))")
    d["clients_new"] = s.scalar(f"count(count by (mac) (last_over_time({clients}[$R])) "
                                f"unless count by (mac) (last_over_time({clients}[$R] offset $R)))")
    rx = s.by("sum by (mac) (increase(unpoller_client_receive_bytes_total[$R]))", "mac")
    tx = s.by("sum by (mac) (increase(unpoller_client_transmit_bytes_total[$R]))", "mac")
    # identity as last seen in the period: names repeat (Mac, iPhone…), so each row
    # also gets the end of its MAC, its IP and its network to tell them apart
    info = {}
    for x in s.prom(f"last_over_time({clients}[$R])") or []:
        m = x["metric"]
        info[m.get("mac", "")] = m
    top = []
    for mac in set(rx) | set(tx):
        m = info.get(mac, {})
        name = m.get("name") or mac
        if re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", name):
            name = f"{m['oui']} device" if m.get("oui") else "Unnamed device"
        detail = " · ".join(x for x in (mac[-5:], m.get("ip", ""), m.get("network", "")) if x)
        top.append({"name": name, "mac": mac, "detail": detail, "bytes": rx.get(mac, 0) + tx.get(mac, 0)})
    d["top_clients"] = sorted(top, key=lambda c: -c["bytes"])[:10]

    # access points
    aps = s.by('max_over_time(sum by (name) (unpoller_device_radio_stations)[$R:5m])', "name")
    traffic = s.by('sum by (name) (increase(unpoller_device_bytes_total{type="uap"}[$R]))', "name")
    sat = s.by("avg_over_time(avg by (name) (unpoller_device_vap_satisfaction_ratio >= 0)[$R:5m])", "name")
    reb = s.by('sum by (name) (resets(unpoller_device_uptime_seconds{type="uap"}[$R]))', "name")
    util = s.prom("avg by (name, band) (avg_over_time(unpoller_device_radio_channel_utilization_total_ratio[$R]))") or []
    u = {}
    for x in util:
        u.setdefault(x["metric"]["name"], {})[x["metric"].get("band")] = float(x["value"][1])
    retries = s.by("sum by (name) (increase(unpoller_device_vap_transmit_retries_total[$R])) / "
                   "sum by (name) (increase(unpoller_device_vap_transmit_packets_total[$R]))", "name")
    signal = s.by("avg_over_time(avg by (name) (unpoller_device_vap_average_client_signal < 0)[$R:5m])", "name")
    d["aps"] = sorted(({"name": n, "peak": aps[n], "bytes": traffic.get(n), "sat": sat.get(n),
                        "u24": u.get(n, {}).get("2.4"), "u5": u.get(n, {}).get("5"),
                        "reboots": reb.get(n, 0), "retries": retries.get(n), "signal": signal.get(n)}
                       for n in aps), key=lambda a: -(a["bytes"] or 0))

    # security
    L = "loki"
    d["blocks_in"] = s.scalar(f"sum(count_over_time({FW_IN} [$R]))", L)
    d["blocks_out"] = s.scalar(f"sum(count_over_time({FW_OUT} [$R]))", L)
    d["block_sources"] = s.scalar(f"count(sum by (src) (count_over_time({FW_IN} [$R])))", L)
    d["top_policies"] = _top(s.by(f"topk(8, sum by (policy) (count_over_time({FW} [$R])))", "policy", L))
    d["top_sources"] = _top(s.by(f"topk(8, sum by (src) (count_over_time({FW_IN} [$R])))", "src", L))
    d["top_devices"] = _top(s.by(f"topk(8, sum by (src) (count_over_time({FW_OUT} [$R])))", "src", L))
    d["threats"] = s.scalar(f"sum(count_over_time({IPS} [$R]))", L)
    d["threats_high"] = s.scalar(f'sum(count_over_time({IPS} | sev=~"HIGH|VERY_HIGH" [$R]))', L)
    d["threats_blocked"] = s.scalar(f'sum(count_over_time({IPS} | evt=~"(?i).*block.*" [$R]))', L)
    d["threats_open"] = (d["threats"] - d["threats_blocked"]) if None not in (d["threats"], d["threats_blocked"]) else None
    d["threat_devices"] = _top(s.by(f"topk(8, sum by (src) (count_over_time({IPS} [$R])))", "src", L))
    d["fw_changes"] = s.scalar(f"sum(count_over_time({AUDIT_FW} [$R]))", L)
    d["logins"] = s.scalar(f"sum(count_over_time({LOGINS} [$R]))", L)
    d["logins_outside"] = s.scalar(f"sum(count_over_time({LOGINS} | ip !~ `{PRIVATE}` [$R]))", L)
    d["login_ips"] = _top(s.by(f"topk(3, sum by (ip) (count_over_time({LOGINS} | ip !~ `{PRIVATE}` [$R])))", "ip", L))
    # the gateway's own internet addresses: logins "from outside" via these come through your own lines
    d["own_ips"] = set(s.by("group by (port_ip) (last_over_time(unpoller_device_port_port_speed_bps[$R]))", "port_ip")) | \
        set(s.by("group by (port_ip) (last_over_time(unpoller_device_port_sfp_rx_power[$R]))", "port_ip"))
    return d


CLIENT_LOG = '{application="unifi_system_log", category="CLIENT_DEVICES"}'
DEVICE_LOG = '{application="unifi_system_log", category="UNIFI_DEVICES"}'
WEAK_DBM = -75   # below this, Wi-Fi is slow and drops; UniFi also treats it as poor


def collect_wifi(s: Source):
    """Wi-Fi experience (Prometheus) and stability (UniFi System Log in Loki)."""
    d, L = {}, "loki"
    wl = 'wired="false"'
    d["sat"] = s.scalar(f"avg_over_time(avg(unpoller_client_satisfaction_ratio{{{wl}}} >= 0)[$R:5m])")
    bands = s.by("avg_over_time(sum by (band) (unpoller_device_radio_stations)[$R:5m])", "band")
    total = sum(bands.values())
    d["band5"] = bands.get("5", 0) / total if total else None
    # devices whose signal averaged weak over the period, worst first, with where they were
    sig = s.by("avg by (mac) (avg_over_time(unpoller_client_radio_signal_db[$R]))", "mac")
    info = {x["metric"].get("mac"): x["metric"] for x in s.prom("last_over_time(unpoller_client_radio_signal_db[$R])") or []}
    weak = []
    for mac, dbm in sig.items():
        if dbm < WEAK_DBM:
            m = info.get(mac, {})
            name = m.get("name") or mac
            if re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", name):
                name = "Unnamed device"
            weak.append({"name": name, "dbm": dbm, "ap": m.get("ap_name", ""),
                         "detail": " · ".join(x for x in (mac[-5:], m.get("ap_name", ""), m.get("essid", "")) if x)})
    d["weak"] = sorted(weak, key=lambda w: w["dbm"])
    d["clients_wireless"] = len(sig)

    ev = lambda e: f'{CLIENT_LOG} |= `"event":"{e}"`'
    d["connects"] = s.scalar(f"sum(count_over_time({ev('CLIENT_CONNECTED_WIRELESS')} [$R]))", L)
    d["disconnects"] = s.scalar(f"sum(count_over_time({ev('CLIENT_DISCONNECTED_WIRELESS')} [$R]))", L)
    d["roams"] = s.scalar(f"sum(count_over_time({ev('CLIENT_ROAMED')} [$R]))", L)
    who = '| json client="parameters.CLIENT.name"'
    d["top_disconnects"] = _top(s.by(f"topk(8, sum by (client) (count_over_time({ev('CLIENT_DISCONNECTED_WIRELESS')} {who} [$R])))", "client", L))
    d["top_roams"] = _top(s.by(f"topk(8, sum by (client) (count_over_time({ev('CLIENT_ROAMED')} {who} [$R])))", "client", L))
    chan = f'{DEVICE_LOG} |= `"event":"AP_CHANGED_CHANNELS"` | json dev="parameters.DEVICE.name"'
    d["channel_changes"] = s.scalar(f"sum(count_over_time({chan} [$R]))", L)
    d["channel_by_ap"] = _top(s.by(f"sum by (dev) (count_over_time({chan} [$R]))", "dev", L))
    conflicts = []
    for stream in s.loki_lines(f'{DEVICE_LOG} |= `"event":"CLIENT_IP_CONFLICT"`', 50) or []:
        for ts, line in stream["values"]:
            conflicts.append({"t": dt.datetime.fromtimestamp(int(ts) / 1e9, TZ), "msg": _json(line).get("message", "")})
    d["ip_conflicts"] = sorted(conflicts, key=lambda c: c["t"])
    return d


DPI_RX = 'unpoller_client_dpi_receive_bytes{mac!="TOTAL"}'
DPI_TX = 'unpoller_client_dpi_transmit_bytes{mac!="TOTAL"}'


def collect_apps(s: Source):
    """Share of traffic by application and category, from UniFi's traffic identification (DPI).

    UniFi reports each device's application traffic as a rolling figure that rises and
    falls, not a running total, so bytes per application over a period can't be summed
    reliably. Averaging the rolling figures over the period gives a fair ranking and share."""
    step = f"{max(60, s.secs // 400)}s"  # ~400 samples per period: 1 min for an hour, ~25 min for a week

    def share(label):
        out = {}
        for m in (DPI_RX, DPI_TX):
            vals = s.by(f"avg_over_time(sum by ({label}) ({m})[$R:{step}])", label)
            if not vals:  # data younger than one sampling step: use the raw samples (few, so cheap)
                vals = s.by(f"sum by ({label}) (sum_over_time({m}[$R]))", label)
            for k, v in vals.items():
                k = re.sub(r"_\d+$", "", k).replace("_", " ")  # UniFi suffixes: "Unknown_255", "Network Protocols_20"
                out[k] = out.get(k, 0) + v
        total = sum(out.values())
        return [{"name": k, "n": v / total} for k, v in sorted(out.items(), key=lambda kv: -kv[1]) if v > 0] if total else []
    d = {"apps": share("application")[:10], "categories": share("category")[:8]}
    d["collecting"] = bool(s.scalar(f"count(last_over_time({DPI_RX}[$R]))"))
    have = s.scalar(f"count_over_time(count({DPI_RX})[$R:{step}])")
    d["coverage"] = min(1.0, (have or 0) / max(1, s.secs // int(step[:-1])))
    p2p = sum(r["n"] for r in d["categories"] if "peer" in r["name"].lower())
    d["p2p"] = p2p or sum(r["n"] for r in d["apps"] if "torrent" in r["name"].lower())
    # which devices the peer-to-peer traffic came from, as a share of it
    who = {}
    for m in (DPI_RX, DPI_TX):
        sel = m.replace("}", ', category=~"Peer-to-Peer.*"}')
        vals = s.prom(f"avg_over_time(sum by (name, mac) ({sel})[$R:{step}])") or \
            s.prom(f"sum by (name, mac) (sum_over_time({sel}[$R]))") or []
        for x in vals:
            k = (x["metric"].get("name", ""), x["metric"].get("mac", ""))
            who[k] = who.get(k, 0) + float(x["value"][1])
    tot = sum(who.values())
    d["p2p_devices"] = [{"name": device_label(n, mac), "share": v / tot}
                        for (n, mac), v in sorted(who.items(), key=lambda kv: -kv[1])[:3]] if tot else []
    return d


def device_label(name, mac):
    """'moto-g06 (…5a:bd)': a name people recognise plus enough MAC to find it in UniFi."""
    if not name or re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", name):
        name = "Unnamed device"
    return f"{name} (…{mac[-5:]})" if mac else name


ISP_EVENTS = {
    "NETWORK_FAILED_OVER_TO_BACKUP_WAN": "Went down, traffic moved to {to}",
    "NETWORK_FAILED_OVER_TO_BACKUP_WAN_TEMPORARY": "Dropped for {dur}, traffic briefly moved to {to}",
    "NETWORK_WAN_RESTORED": "Back up after {dur} on {to}",
    "ISP_HIGH_LATENCY": "High latency{lat}",
    "ISP_PACKET_LOSS": "Packet loss",
    "NETWORK_WAN_FAILED_MULTIPLE_TIMES": "Went down {count} times in 24 hours ({dur} in total)",
}


def collect_isp(s: Source):
    """Per internet line: state, availability, speed tests, data carried and ISP events from the System Log."""
    by = lambda q, lbl: s.by(q, lbl)
    state = {}
    for x in s.prom("max by (wan_interface, wan_networkgroup, state) (last_over_time(unpoller_wan_interface_state[$R]))") or []:
        m = x["metric"]
        state[m.get("wan_networkgroup")] = {"name": m.get("wan_interface"), "state": (m.get("state") or "").capitalize()}
    up = by("avg_over_time(max by (wan_networkgroup) (unpoller_wan_uptime_percentage >= 0)[$R:5m])", "wan_networkgroup")
    promised = by("max by (wan_networkgroup) (last_over_time(unpoller_wan_provider_download_kbps[$R]))", "wan_networkgroup")
    dl = by("avg_over_time(max by (wan_group) (unpoller_speedtest_download_mbps)[$R:15m])", "wan_group")
    dl_min = by("min_over_time(max by (wan_group) (unpoller_speedtest_download_mbps)[$R:15m])", "wan_group")
    ul = by("avg_over_time(max by (wan_group) (unpoller_speedtest_upload_mbps)[$R:15m])", "wan_group")
    lat = by("avg_over_time(max by (wan_group) (unpoller_speedtest_latency_ms)[$R:15m])", "wan_group")
    carried = by("sum by (port_name) (increase(unpoller_device_port_receive_bytes_total[$R]) + "
                 "increase(unpoller_device_port_transmit_bytes_total[$R]))", "port_name")
    lines = {}
    for wan, st in state.items():
        lines[st["name"]] = {"name": st["name"], "wan": wan, "state": st["state"], "up": up.get(wan),
                             "promised": (promised.get(wan) or 0) / 1000 or None, "dl": dl.get(wan),
                             "dl_min": dl_min.get(wan), "ul": ul.get(wan), "lat": lat.get(wan),
                             "carried": carried.get(st["name"]), "outages": 0, "down_s": 0,
                             "latency": 0, "loss": 0}

    events = []
    for stream in s.loki_lines(WAN_EVENTS, 1000) or []:
        for ts, raw in stream["values"]:
            j = _json(raw)
            evt = j.get("event", "")
            if evt not in ISP_EVENTS:
                continue
            prm = j.get("parameters", {})
            val = lambda k, f="name": (prm.get(k) or {}).get(f) or ""
            line = val("WAN_NAME") or val("WAN_NAME", "id")
            secs = int(val("DURATION", "id") or 0)
            text = ISP_EVENTS[evt].format(to=val("FAILOVER_WAN_NAME") or "a backup line", dur=val("DURATION") or "a while",
                                          lat=f" ({val('LATENCY')} ms)" if val("LATENCY") else "", count=val("COUNT") or "several")
            events.append({"t": dt.datetime.fromtimestamp(int(ts) / 1e9, TZ), "line": line, "msg": text, "evt": evt})
            ln = lines.setdefault(line, {"name": line, "wan": "", "state": "", "up": None, "promised": None, "dl": None,
                                         "dl_min": None, "ul": None, "lat": None, "carried": None,
                                         "outages": 0, "down_s": 0, "latency": 0, "loss": 0})
            if evt in ("NETWORK_FAILED_OVER_TO_BACKUP_WAN", "NETWORK_FAILED_OVER_TO_BACKUP_WAN_TEMPORARY"):
                ln["outages"] += 1
            if evt in ("NETWORK_WAN_RESTORED", "NETWORK_FAILED_OVER_TO_BACKUP_WAN_TEMPORARY"):
                ln["down_s"] += secs  # each outage ends with one of these, carrying its length
            if evt == "ISP_HIGH_LATENCY":
                ln["latency"] += 1
            if evt == "ISP_PACKET_LOSS":
                ln["loss"] += 1
    return {"lines": sorted(lines.values(), key=lambda l: l["wan"] or "~"), "events": sorted(events, key=lambda e: e["t"])}


def fduration(secs):
    secs = int(secs or 0)
    if secs < 60:
        return f"{secs} s"
    if secs < 3600:
        return f"{secs // 60} min"
    h, m = divmod(secs // 60, 60)
    return f"{h} h {m} min" if m else f"{h} h"


# ---------------------------------------------------------------- maintenance

def collect_maint(s: Source):
    """Housekeeping: backups, updates, storage, address pools and fibre modules."""
    d = {}
    d["backup"] = s.scalar("max(last_over_time(unpoller_controller_autobackup_enabled[$R]))")
    d["controller_update"] = s.scalar("max(last_over_time(unpoller_controller_update_available[$R]))")
    ver = {x["metric"].get("name"): x["metric"] for x in s.prom("last_over_time(unpoller_device_info[$R])") or []}
    d["updates"] = [{"name": n, "model": ver.get(n, {}).get("model", ""), "version": ver.get(n, {}).get("version", "")}
                    for n, v in sorted(s.by("max by (name) (last_over_time(unpoller_device_upgradable[$R]))", "name").items()) if v >= 1]
    d["devices"] = len(ver)
    size = s.by('max by (name, storage_name) (last_over_time(unpoller_device_storage{storage_reading="size"}[$R]))', "storage_name")
    used = s.by('max by (name, storage_name) (last_over_time(unpoller_device_storage{storage_reading="used"}[$R]))', "storage_name")
    d["storage"] = [{"name": k, "used": used.get(k, 0), "size": v, "pct": used.get(k, 0) / v if v else None}
                    for k, v in sorted(size.items())]
    peak = s.by("max by (network) (max_over_time(unpoller_dhcp_utilization_percent[$R]))", "network")
    pool = s.by("max by (network) (last_over_time(unpoller_dhcp_pool_size[$R]))", "network")
    leases = s.by("max by (network) (max_over_time(unpoller_dhcp_active_leases[$R]))", "network")
    d["pools"] = sorted(({"network": n, "peak": v / 100, "size": pool.get(n), "leases": leases.get(n)}
                         for n, v in peak.items()), key=lambda x: -x["peak"])
    first = s.secs - 86400
    rx_end = s.by("avg by (port_name) (avg_over_time(unpoller_device_port_sfp_rx_power[1d]))", "port_name")
    rx_start = s.by(f"avg by (port_name) (avg_over_time(unpoller_device_port_sfp_rx_power[1d] offset {first}s))", "port_name") if first > 0 else {}
    rx_min = s.by("min by (port_name) (min_over_time(unpoller_device_port_sfp_rx_power[$R]))", "port_name")
    temp = s.by("max by (port_name) (max_over_time(unpoller_device_port_sfp_temperature[$R]))", "port_name")
    d["sfp"] = [{"port": p, "rx": v, "rx_start": rx_start.get(p), "rx_min": rx_min.get(p), "temp": temp.get(p),
                 "drop": (rx_start[p] - v) if p in rx_start else None} for p, v in sorted(rx_end.items())]
    return d


SFP_WEAK_DBM = -20   # most 1G single-mode modules stop working around -20 to -24 dBm
SFP_DROP_DB = 2      # a fall this large over a period means the fibre or a connector is degrading


def _top(m):
    return [{"name": k or "unknown", "n": v} for k, v in sorted(m.items(), key=lambda kv: -kv[1])]


def events(s: Source, wan=True):
    """Notable events: each internet failover, and firewall changes grouped per admin per day."""
    out, changes = [], {}
    for stream in (s.loki_lines(f'{WAN_EVENTS} |= `FAILED_OVER`', 200) if wan else None) or []:
        for ts, line in stream["values"]:
            t = dt.datetime.fromtimestamp(int(ts) / 1e9, TZ)
            out.append({"t": t, "kind": "Internet", "msg": _json(line).get("message", line)})
    for stream in s.loki_lines(AUDIT_FW, 2000) or []:
        for ts, line in stream["values"]:
            t = dt.datetime.fromtimestamp(int(ts) / 1e9, TZ)
            prm = _json(line).get("parameters", {})
            admin = prm.get("ADMIN", {}).get("name", "someone")
            obj = prm.get("OBJECT", {}).get("name", "")
            g = changes.setdefault((admin, t.date()), {"t": t, "n": 0, "objs": []})
            g["n"] += 1
            if obj and obj not in g["objs"]:
                g["objs"].append(obj)
    for (admin, _), g in changes.items():
        objs = ", ".join(g["objs"][:6]) + (f" and {len(g['objs']) - 6} more" if len(g["objs"]) > 6 else "")
        what = f"{len(g['objs'])} rule{'s' * (len(g['objs']) != 1)}" if g["objs"] else "firewall settings"
        out.append({"t": g["t"], "kind": "Firewall change",
                    "msg": f"{admin} made {g['n']} change{'s' * (g['n'] != 1)} to {what}" + (f": {objs}" if objs else "")})
    return sorted(out, key=lambda e: e["t"])


def _json(line):
    try:
        return json.loads(line)
    except ValueError:
        return {}


def threat_list(s: Source, limit=30):
    """Individual IPS events, newest first: when, device, target, severity, blocked or not."""
    rows = []
    for stream in s.loki_lines(IPS.split(" | json")[0], 1000) or []:
        for ts, line in stream["values"]:
            j = _json(line)
            prm = j.get("parameters", {})
            name = lambda k: prm.get(k, {}).get("name") or prm.get(k, {}).get("id") or ""
            rows.append({"t": dt.datetime.fromtimestamp(int(ts) / 1e9, TZ),
                         "src": name("SRC_CLIENT") or name("SRC_IP"),
                         "dst": name("DST_CLIENT") or name("DST_IP") or name("DST_DEVICE"),
                         "sev": (j.get("severity") or "").replace("_", " ").capitalize(),
                         "blocked": "BLOCK" in (j.get("event") or "").upper()})
    rows.sort(key=lambda r: r["t"], reverse=True)
    return rows[:limit], len(rows)


def wan_series(s: Source, kind):
    days = (s.end - s.start).days
    step = 3600 if days <= 10 else 6 * 3600 if days <= 45 else 86400
    w = f"{step}s"
    down = s.prom_range(f'max(avg_over_time(unpoller_site_receive_rate_bytes{{subsystem="wan"}}[{w}]))*8', step) or []
    up = s.prom_range(f'max(avg_over_time(unpoller_site_transmit_rate_bytes{{subsystem="wan"}}[{w}]))*8', step) or []
    pts = lambda r: [(float(t), float(v)) for t, v in r[0]["values"]] if r else []
    return pts(down), pts(up), step


# ---------------------------------------------------------------- formatting

def fbytes(b):
    if b is None:
        return "–"
    for unit, size in (("TB", 1e12), ("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if b >= size:
            return f"{b / size:.{1 if b / size < 100 else 0}f} {unit}"
    return f"{b:.0f} B"


def fbits(b):
    if b is None:
        return "–"
    for unit, size in (("Gbps", 1e9), ("Mbps", 1e6), ("kbps", 1e3)):
        if b >= size:
            return f"{b / size:.{1 if b / size < 100 else 0}f} {unit}"
    return f"{b:.0f} bps"


def fnum(n):
    return "–" if n is None else f"{n:,.0f}"


def fpct(r, digits=0):
    return "–" if r is None else f"{r * 100:.{digits}f}%"


def change(cur, prev, unit_word, period_word, covered):
    """'+12% vs last week' / '3 fewer vs last week' / reason it can't be compared."""
    if not covered:
        return f"no earlier {period_word} to compare"
    if cur is None or prev is None:
        return ""
    vs = f"vs last {period_word}"
    if prev == 0:
        return "none last " + period_word if cur else f"none, same as last {period_word}"
    diff = cur - prev
    if abs(prev) < 20:  # small counts read better as absolute numbers
        if diff == 0:
            return f"same as last {period_word}"
        word = "more" if diff > 0 else "fewer"
        return f"{abs(diff):.0f} {word} {vs}"
    pct = diff / prev * 100
    if abs(pct) < 0.5:
        return f"same as last {period_word}"
    return f"{'+' if pct > 0 else '−'}{abs(pct):.0f}% {vs}"


# ---------------------------------------------------------------- chart

def svg_chart(down, up, kind, start, end, w=658, h=236):
    """WAN throughput line chart as inline SVG (two series, one shared axis)."""
    if not down:
        return '<p class="muted">No throughput data for this period</p>'
    L, R, T, B = 44, 12, 28, 28
    pw, ph = w - L - R, h - T - B
    t0, t1 = start.timestamp(), end.timestamp()
    vmax = max(v for _, v in down + up) or 1
    # nice axis maximum in Mbps
    mb = vmax / 1e6
    stepv = 10 ** math.floor(math.log10(mb / 4)) if mb > 0 else 1
    for m in (1, 2, 2.5, 5, 10):
        if mb / (stepv * m) <= 4:
            stepv *= m
            break
    top = stepv * math.ceil(mb / stepv) * 1e6
    x = lambda t: L + (t - t0) / (t1 - t0) * pw
    y = lambda v: T + ph - v / top * ph
    path = lambda pts: "M" + " L".join(f"{x(t):.1f},{y(v):.1f}" for t, v in pts)
    g = []
    # grid + y labels
    v = 0.0
    while v <= top + 1:
        yy = y(v)
        g.append(f'<line x1="{L}" x2="{w - R}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="#1b1b1a" stroke-opacity="0.08" stroke-width="1"/>')
        g.append(f'<text x="{L - 8}" y="{yy + 3:.1f}" font-family="PlexMono" font-size="7pt" fill="#8a8984" text-anchor="end">{v / 1e6:.0f}</text>')
        v += stepv * 1e6
    g.append(f'<text x="{L - 8}" y="{T - 14}" font-family="PlexMono" font-size="7pt" fill="#8a8984" text-anchor="end">Mbps</text>')
    # x labels
    day = start
    while day < end:
        xx = x(day.timestamp())
        if (end - start).days <= 10:
            g.append(f'<line x1="{xx:.1f}" x2="{xx:.1f}" y1="{T}" y2="{T + ph}" stroke="#1b1b1a" stroke-opacity="0.08" stroke-width="1"/>')
            g.append(f'<text x="{xx + pw / max(1, (end - start).days) / 2:.1f}" y="{h - 8}" font-family="PlexMono" font-size="7pt" fill="#8a8984" text-anchor="middle">{day:%a %-d}</text>')
            day += dt.timedelta(days=1)
        else:
            stride = 7 if (end - start).days <= 45 else 14
            g.append(f'<text x="{xx:.1f}" y="{h - 8}" font-family="PlexMono" font-size="7pt" fill="#8a8984" text-anchor="middle">{day:%-d %b}</text>')
            day += dt.timedelta(days=stride)
    # series: download leads in the accent, upload sits in a quiet neutral
    g.append(f'<path d="{path(up)}" fill="none" stroke="#a9a8a2" stroke-width="1.4" stroke-linejoin="round"/>')
    g.append(f'<path d="{path(down)}" fill="none" stroke="#2a78d6" stroke-width="1.6" stroke-linejoin="round"/>')
    # direct label at the peak of download
    pt, pv = max(down, key=lambda p: p[1])
    px, py = x(pt), y(pv)
    when = dt.datetime.fromtimestamp(pt, TZ)
    anchor = "end" if px > L + pw * 0.75 else "start"
    dx = -8 if anchor == "end" else 8
    g.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="#2a78d6" stroke="#ffffff" stroke-width="2"/>')
    g.append(f'<text x="{px + dx:.1f}" y="{py + 4:.1f}" font-family="Plex" font-weight="500" font-size="7.5pt" fill="#1b1b1a" text-anchor="{anchor}">'
             f'Peak {fbits(pv)}, {when:%a %H:%M}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
            f'aria-label="Internet throughput, download and upload">{"".join(g)}</svg>')


# ---------------------------------------------------------------- sections

# What a report can include; admins switch these on and off in the portal.
SECTIONS = [
    ("summary", "Summary", "A few plain-language sentences and anything that needs attention"),
    ("figures", "Headline numbers", "Data downloaded, peak devices, blocks and threats, with the change from last time"),
    ("downloaded", "Data downloaded", "Data downloaded and uploaded: the headline number, the summary sentence and the Internet and gateway rows"),
    ("gap_note", "Missing-data note", "A note when metrics weren't collected for part of the period"),
    ("throughput", "Internet throughput chart", "Download and upload over the period"),
    ("internet", "Internet and gateway", "Data used, latency, failovers, internet lines, gateway CPU, memory and temperature"),
    ("isp", "Internet lines and quality", "Per line: active or backup, availability, outages and their length, ISP latency and packet-loss events, speed tests, data carried"),
    ("devices", "Devices", "Devices online and the ten that used the most data"),
    ("aps", "Access points", "Traffic, clients, Wi-Fi experience and channel use per access point"),
    ("apps", "Applications", "Top applications and categories by traffic (needs DPI on in UniFi and SAVE_DPI=true in unpoller/.env)"),
    ("wifi", "Wi-Fi experience and stability", "Satisfaction, weak-signal devices, 2.4/5 GHz split, disconnections, roaming, channel changes, IP conflicts"),
    ("security", "Firewall and security", "Blocked connections, top blocking rules and sources, threat totals, admin logins"),
    ("threats", "Threats blocked", "Each threat intrusion prevention detected, with device, target and severity"),
    ("maintenance", "Maintenance", "Automatic backups, firmware updates, storage, address pools filling up, fibre module signal"),
    ("events", "Changes and events", "Firewall changes by admins and internet failovers"),
]


# ---------------------------------------------------------------- summary

def summary(d, p, word, covered, on=None):
    """Plain-language lines for page 1, written from the numbers.
    Only covers the sections that are switched on."""
    on = on if on is not None else {k for k, *_ in SECTIONS}
    lines = []
    up = [w for w in d["wans"] if w["uptime"] is not None]
    if up and "internet" in on:
        prim = max(up, key=lambda w: w["uptime"])
        fo = d["failovers"] or 0
        tail = (f" It failed over to a backup line {fo:.0f} time{'s' * (fo != 1)}"
                if fo else " There were no failovers to the backup lines")
        lines.append(f"The main internet line ({prim['name']}) was up {prim['uptime']:.2f}% of the {word}.{tail}.")
    isp = d.get("isp")
    if isp and "isp" in on:
        probs = []
        for l in isp["lines"]:
            bits = []
            if l["outages"]:
                bits.append(f"went down {_times(l['outages'])}"
                            + (f" ({fduration(l['down_s'])} in total)" if l["down_s"] else ""))
            if l["latency"]:
                bits.append(f"had high latency {_times(l['latency'])}")
            if l["loss"]:
                bits.append(f"lost packets {_times(l['loss'])}")
            if bits:
                probs.append(f"{l['name']} " + _join(bits))
        lines.append(("Internet lines: " + "; ".join(probs) + ".") if probs else
                     f"No internet line reported outages, high latency or packet loss this {word}.")
    if d["wan_down"] is not None and "downloaded" in on:
        ch = change(d["wan_down"], p.get("wan_down"), "", word, covered["prom"])
        lines.append(f"The network downloaded {fbytes(d['wan_down'])} and uploaded {fbytes(d['wan_up'])}"
                     + (f" ({ch})" if ch and not ch.startswith("no earlier") else "") + ".")
    if d["clients_peak"] is not None and on & {"devices", "aps"}:
        new = d["clients_new"] if covered["prom"] else None
        lines.append(f"{fnum(d['clients_peak'])} devices were online at the busiest moment, "
                     f"{fnum(d['clients_unique'])} different devices over the {word}"
                     + (f", {fnum(new)} of them seen for the first time" if new else "") + ".")
    if d["blocks_in"] is not None and on & {"security", "threats"}:
        lines.append(f"The firewall blocked {fnum(d['blocks_in'])} connection attempts from the internet "
                     f"and {fnum(d['blocks_out'])} from devices inside the network. "
                     f"Intrusion prevention blocked {fnum(d['threats_blocked'])} of {fnum(d['threats'])} threats"
                     + ((", all of them high severity" if d["threats_high"] == d["threats"] else
                            f", {fnum(d['threats_high'])} of them high severity") if d["threats_high"] else "") + ".")
    return lines


def _times(n):
    n = int(n)
    return "once" if n == 1 else "twice" if n == 2 else f"{n} times"


def _who(x):
    """A device name, MAC or IP from the logs, in the report's usual 'name (…ab:cd)' form."""
    if re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", x or ""):
        return f"Unnamed device (…{x[-5:]})"
    return x


def _join(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def attention(d, on=None):
    """Things a reader should act on, limited to the sections switched on."""
    on = on if on is not None else {k for k, *_ in SECTIONS}
    a = []
    gw, aps, sec = "internet" in on, "aps" in on, bool(on & {"security", "threats"})
    if gw and d["gw_reboots"]:
        a.append(f"The gateway restarted {_times(d['gw_reboots'])}")
    for ap in d["aps"] if aps else []:
        if ap["reboots"]:
            a.append(f"Access point {ap['name']} restarted {_times(ap['reboots'])}")
    if gw and d["mem_end"] is not None and d["mem_start"] is not None and d["mem_end"] - d["mem_start"] > 0.05:
        a.append(f"Gateway memory use climbed from {fpct(d['mem_start'])} to {fpct(d['mem_end'])}")
    if gw and d["mem_max"] and d["mem_max"] > 0.9:
        a.append(f"Gateway memory peaked at {fpct(d['mem_max'])}")
    if sec and d["threats_open"]:
        a.append(f"{fnum(d['threats_open'])} threats were detected but not blocked, see Security")
    if sec and d["threats_high"]:
        top = d["threat_devices"][0] if d["threat_devices"] else None
        a.append(f"{fnum(d['threats_high'])} high-severity threats were detected"
                 + (", all blocked" if not d["threats_open"] else "")
                 + (f"; {top['n']:.0f} of them involved {_who(top['name'])}" if top else ""))
    if "security" in on and d["logins_outside"]:
        own = d.get("own_ips", set())
        ips = ", ".join(f"{r['name']} ({r['n']:.0f}" + (", your own internet address" if r["name"] in own else "") + ")"
                        for r in d.get("login_ips", []))
        a.append(f"{fnum(d['logins_outside'])} admin logins came from outside the local network"
                 + (f", from {ips}; check these are your admins" if ips else ""))
    if aps:
        busy = sorted((ap for ap in d["aps"] if (ap["u24"] or 0) > 0.6), key=lambda ap: -ap["u24"])
        if busy:
            a.append("2.4 GHz channel busy most of the time on " + _join(f"{ap['name']} ({fpct(ap['u24'])})" for ap in busy)
                     + "; devices there will feel slow")
        resend = sorted((ap for ap in d["aps"] if (ap.get("retries") or 0) > 0.15), key=lambda ap: -ap["retries"])
        if resend:
            a.append("Access points resending many Wi-Fi packets (healthy is under 15%): "
                     + _join(f"{ap['name']} {fpct(ap['retries'])}" for ap in resend)
                     + "; a sign of interference or devices with weak signal")
        weak_ap = sorted((ap for ap in d["aps"] if ap.get("signal") is not None and ap["signal"] < WEAK_DBM),
                         key=lambda ap: ap["signal"])
        if weak_ap:
            a.append("Devices averaged a weak signal on " + _join(f"{ap['name']} ({ap['signal']:.0f} dBm)" for ap in weak_ap)
                     + "; they're too far from the access point or behind walls")
    a_ = d.get("dpi")
    if a_ and "apps" in on and a_["p2p"] > 0.05:
        who = a_.get("p2p_devices") or []
        src = ""
        if who:
            first = who[0]
            src = (f", nearly all from {first['name']}" if first["share"] > 0.9 else
                   "; mostly from " + _join(f"{w['name']} ({fpct(w['share'])})" for w in who))
        a.append(f"Peer-to-peer file sharing (such as BitTorrent) was {fpct(a_['p2p'])} of identified traffic{src}; "
                 "check it's allowed, it often carries pirated content and malware")
    w = d.get("wifi")
    if w and "wifi" in on:
        if w["weak"]:
            n = len(w["weak"])
            names = _join(f"{x['name']}" + (f" on {x['ap']}" if x["ap"] else "") for x in w["weak"][:3])
            a.append(f"{n} device{'s' * (n != 1)} had a weak Wi-Fi signal on average (below {WEAK_DBM} dBm): {names}"
                     + (f" and {n - 3} more" if n > 3 else ""))
        if w["sat"] is not None and w["sat"] < 0.8:
            a.append(f"Average Wi-Fi experience was {fpct(w['sat'])}, below the 80% UniFi treats as good")
        days = max(1, d.get("_days", 7))
        for r in w["channel_by_ap"]:
            if r["n"] / days > 3:
                a.append(f"{r['name']} changed channel {r['n']:.0f} times, a sign of interference near it")
        if w["ip_conflicts"]:
            n = len(w["ip_conflicts"])
            a.append(f"{n} IP address conflict{'s' * (n != 1)} on the network: {w['ip_conflicts'][-1]['msg']}")
    isp = d.get("isp")
    if isp and "isp" in on:
        for l in isp["lines"]:
            if l["outages"]:
                a.append(f"Internet line {l['name']} went down {_times(l['outages'])}"
                         + (f", {fduration(l['down_s'])} in total" if l["down_s"] else ""))
            if l["latency"] + l["loss"] >= 3:
                a.append(f"{l['name']} reported high latency {l['latency']} and packet loss {l['loss']} times; "
                         "worth raising with the provider")
            if l["promised"] and l["dl"] and l["dl"] < 0.5 * l["promised"]:
                a.append(f"{l['name']} speed tests averaged {l['dl']:.0f} Mbps, under half the {l['promised']:.0f} Mbps "
                         "the plan promises")
            if l["state"] and l["state"] not in ("Active", "Backup"):
                a.append(f"Internet line {l['name']} is {l['state'].lower()}")
    m = d.get("maint")
    if m and "maintenance" in on:
        if m["backup"] is not None and m["backup"] < 1:
            a.append("Automatic backups are off on the UniFi console; turn them on under Settings, Control Plane, "
                     "Backups so a failed console can be restored")
        if m["controller_update"]:
            a.append("A UniFi Network update is available for the console")
        if m["updates"]:
            a.append(f"Firmware updates are waiting for {_join(u['name'] for u in m['updates'][:4])}"
                     + (f" and {len(m['updates']) - 4} more" if len(m["updates"]) > 4 else ""))
        for p_ in m["pools"]:
            if p_["peak"] >= 0.8:
                a.append(f"The {p_['network']} address pool reached {fpct(p_['peak'])} full; new devices can't "
                         "connect once it's full, so enlarge it or shorten the lease time")
        for st in m["storage"]:
            if (st["pct"] or 0) >= 0.85:
                a.append(f"Console storage '{st['name']}' is {fpct(st['pct'])} full")
        for f in m["sfp"]:
            if f["rx"] is not None and f["rx"] < SFP_WEAK_DBM:
                a.append(f"The fibre module on {f['port']} receives a weak {f['rx']:.1f} dBm; clean or check the fibre")
            elif f["drop"] is not None and f["drop"] >= SFP_DROP_DB:
                a.append(f"The fibre signal on {f['port']} fell {f['drop']:.1f} dB over the {d.get('_word', 'period')}; "
                         "a connector or the fibre may be degrading")
            if (f["temp"] or 0) >= 70:
                a.append(f"The fibre module on {f['port']} reached {f['temp']:.0f} °C")
    return a


# ---------------------------------------------------------------- build

def build(kind, end=None, start=None):
    st = load_settings()  # applies the time zone before any dates are worked out
    end = end or period_end(kind, dt.datetime.now(TZ))
    start = start or period_start(kind, end)
    prev_start = period_start(kind, start, end - start)
    word = KINDS[kind]
    title, dates = period_label(kind, start, end)
    name = st["general"]["name"].strip()
    on = {k for k, v in st["sections"].items() if v}
    log.info("building %s report %s (%s), sections: %s", kind, title, dates, ",".join(sorted(on)))

    cur, prev = Source(start, end), Source(prev_start, start)
    d, p = collect(cur), collect(prev)
    d["_days"] = (end - start).days
    d["wifi"] = collect_wifi(cur) if "wifi" in on else None
    d["dpi"] = collect_apps(cur) if "apps" in on else None
    d["isp"] = collect_isp(cur) if "isp" in on else None
    d["maint"] = collect_maint(cur) if "maintenance" in on else None
    d["_word"] = word
    cov = prev.coverage()
    gap = 1 - cur.coverage()["prom"]
    covered = {k: v >= 0.95 for k, v in cov.items()}
    down, up, step = wan_series(cur, kind) if "throughput" in on else ([], [], 0)
    ch = lambda key, src="prom": change(d.get(key), p.get(key), "", word, covered[src])

    # headline numbers follow the sections that are on; if none match, show them all
    figs = [
        ("Downloaded", fbytes(d["wan_down"]), ch("wan_down"), {"downloaded"}),
        ("Peak devices online", fnum(d["clients_peak"]), ch("clients_peak"), {"devices", "aps"}),
        ("Blocked from the internet", fnum(d["blocks_in"]), ch("blocks_in", "loki"), {"security"}),
        ("Threats blocked", fnum(d["threats_blocked"]), ch("threats_blocked", "loki"), {"security", "threats"}),
    ]
    if "downloaded" not in on:
        figs = figs[1:]  # never show it when unticked, even in the show-all fallback
    figs = [f[:3] for f in figs if f[3] & on] or [f[:3] for f in figs]
    page1 = bool(on & {"summary", "figures", "throughput"})

    env = Environment(loader=FileSystemLoader(HERE), autoescape=True)
    env.filters.update(bytes=fbytes, bits=fbits, num=fnum, pct=fpct, duration=fduration)
    html_out = env.get_template("template.html").render(
        name=name, on=on, figs=figs, page1=page1, aps_break=page1 or bool(on & {"internet", "devices"}), kind=kind, word=word, title=title, dates=dates,
        tzname=str(TZ), generated=dt.datetime.now(TZ), d=d, p=p, ch=ch, covered=covered,
        summary=summary(d, p, word, covered, on), attention=attention(d, on),
        events=events(cur, wan="isp" not in on) if "events" in on else [],
        threats=threat_list(cur) if "threats" in on else ([], 0),
        chart=svg_chart(down, up, kind, start, end) if "throughput" in on else "", fonts=(HERE / "fonts").as_uri(),
        errors=cur.errors + prev.errors, gap=gap, max_client=max([c["bytes"] for c in d["top_clients"]] or [1]),
    )
    OUT.mkdir(parents=True, exist_ok=True)
    pdf = report_path(kind, start, end)
    from weasyprint import HTML  # imported late: slow to load, not needed until a report is due
    tmp = pdf.with_suffix(".part")
    HTML(string=html_out, base_url=str(HERE)).write_pdf(tmp)
    tmp.replace(pdf)  # appears in the portal only once complete
    log.info("wrote %s (%d query errors)", pdf, len(cur.errors + prev.errors))
    what = f"{kind.capitalize()} report, {title}" if kind != "custom" else f"Report for {title}"
    subject = f"{name}: {what} ({dates})" if name else f"{what} ({dates})"
    return pdf, subject, summary(d, p, word, covered, on), len(cur.errors + prev.errors)


# ---------------------------------------------------------------- settings

SETTINGS = OUT / "settings.json"


def default_settings():
    """First-run values come from the environment; after that settings.json wins."""
    env = os.environ.get
    to = env("REPORT_TO", "")
    return {
        "general": {"name": env("REPORT_NAME", ""), "timezone": env("TZ") or "UTC"},
        "sections": {k: True for k, *_ in SECTIONS},
        "smtp": {"host": env("SMTP_HOST", ""), "port": int(env("SMTP_PORT", 587)),
                 "security": env("SMTP_SECURITY", "starttls"), "user": env("SMTP_USER", ""),
                 "password": env("SMTP_PASSWORD", ""), "from": env("SMTP_FROM", "")},
        "recipients": {k: to for k in ("weekly", "monthly", "quarterly")},
        "schedule": {"enabled": True, "hour": int(env("SEND_HOUR", 7)),
                     "kinds": env("REPORTS", "weekly,monthly,quarterly").split(",")},
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
    return sorted(z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/", "posix/", "right/"))) + ["UTC"]


def display_name(s=None):
    """What the portal calls itself: the admin's chosen name, or Reports."""
    return (s or load_settings())["general"]["name"].strip() or "Reports"


def save_settings(s):
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2))
    os.chmod(tmp, 0o600)  # holds the SMTP password
    tmp.replace(SETTINGS)


def split_addresses(text):
    return [a.strip() for a in re.split(r"[,;\s]+", text or "") if a.strip()]


# ---------------------------------------------------------------- notifications

# What happened (reports made, emails sent or failed, settings changed), shown
# in the portal's notification list. Read state is kept per user.
NOTES = OUT / "notifications.json"
READS = OUT / "notifications-read.json"
_notes_lock = threading.Lock()


def _read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return default


def _write_json(path, data):
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def notify(text, level="info", by=None, file=None, admins_only=False, for_user=None):
    """level: info or error. admins_only notes are shown to admins and to for_user."""
    note = {"id": os.urandom(6).hex(), "t": dt.datetime.now(TZ).isoformat(), "text": text, "level": level,
            "by": by, "file": file, "admins_only": admins_only, "for_user": for_user}
    with _notes_lock:
        notes = _read_json(NOTES, [])
        notes.append(note)
        _write_json(NOTES, notes[-300:])
    log.info("notification: %s", text)


def notes_for(login, admin):
    """Newest first, with a read flag for this user."""
    with _notes_lock:
        notes = _read_json(NOTES, [])
        read = set(_read_json(READS, {}).get(login, []))
    out = []
    for n in reversed(notes):
        if n.get("admins_only") and not admin and n.get("for_user") != login:
            continue
        out.append({**n, "read": n["id"] in read})
    return out


def mark_read(login, ids):
    with _notes_lock:
        reads = _read_json(READS, {})
        live = {n["id"] for n in _read_json(NOTES, [])}
        reads[login] = sorted((set(reads.get(login, [])) | set(ids)) & live)  # forget ids of pruned notes
        _write_json(READS, reads)


# ---------------------------------------------------------------- email

def send_mail(to, subject, text, html_body=None, attachment=None, smtp=None):
    smtp = smtp or load_settings()["smtp"]
    if not smtp.get("host"):
        raise RuntimeError("no SMTP server is set up")
    to = split_addresses(to) if isinstance(to, str) else to
    if not to:
        raise RuntimeError("no recipients")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, smtp.get("from") or smtp.get("user"), ", ".join(to)
    msg.set_content(text)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    if attachment:
        msg.add_attachment(attachment.read_bytes(), maintype="application", subtype="pdf", filename=attachment.name)
    port, sec = int(smtp.get("port") or 587), smtp.get("security", "starttls")
    cls = smtplib.SMTP_SSL if sec == "ssl" else smtplib.SMTP
    with cls(smtp["host"], port, timeout=60) as c:
        if sec == "starttls":
            c.starttls()
        if smtp.get("user"):
            c.login(smtp["user"], smtp.get("password", ""))
        c.send_message(msg)
    log.info("emailed %r to %s", subject, ", ".join(to))


def mail_error(e):
    """Plain-language reason an email failed."""
    import socket
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return "the mail server rejected the username or password (for Gmail, use an app password)"
    if isinstance(e, ConnectionRefusedError):
        return "the mail server refused the connection; check the server name and port"
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "the mail server didn't answer; check the server name, port and security setting"
    if isinstance(e, socket.gaierror):
        return "the mail server name couldn't be found; check the SMTP server setting"
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        return f"the mail server refused the address(es): {', '.join(e.recipients)}"
    if isinstance(e, (smtplib.SMTPNotSupportedError, ssl_error_types())):
        return "the security setting doesn't match the mail server; try STARTTLS on port 587 or SSL/TLS on 465"
    return str(e) or e.__class__.__name__


def ssl_error_types():
    import ssl
    return ssl.SSLError


def email_report(pdf, subject, lines, to):
    send_mail(to, subject, "\n\n".join(lines) + "\n\nThe full report is attached.",
              "".join(f"<p>{html.escape(x)}</p>" for x in lines)
              + "<p style='color:#6b6a66'>The full report is attached.</p>", pdf)


def run(kind, end=None, send=True, start=None, to=None):
    """Build a report; email it to `to`, or to the kind's recipients when send=True."""
    pdf, subject, lines, errors = build(kind, end, start)
    what = report_title(kind, pdf)
    if to is None and send:
        to = load_settings()["recipients"].get(kind, "")
    if to:
        try:
            email_report(pdf, subject, lines, to)
        except Exception as e:  # noqa: BLE001 - the PDF is saved either way
            log.exception("email failed; report kept at %s", pdf)
            notify(f"{what} was made from the command line, but emailing it to {to} failed: {mail_error(e)}",
                   "error", file=pdf.name, admins_only=True)
            raise
        notify(f"{what} was made from the command line and emailed to {to}", file=pdf.name)
    else:
        notify(f"{what} was made from the command line", file=pdf.name)
    return pdf


def report_title(kind, pdf_or_start, end=None):
    """'Weekly report, Week 44 (26 Oct – 1 Nov 2026)' from a report file, or from a period start/end."""
    if isinstance(pdf_or_start, Path):
        m = re.match(r"\w+-(\d{4}-\d{2}-\d{2})(?:-to-(\d{4}-\d{2}-\d{2}))?\.pdf$", pdf_or_start.name)
        day = lambda x: dt.datetime.combine(dt.date.fromisoformat(x), dt.time(), TZ)
        start = day(m[1])
        end = day(m[2]) + dt.timedelta(days=1) if m[2] else _next_period(kind, start)
    else:
        start = pdf_or_start
    title, dates = period_label(kind, start, end)
    if kind == "custom":
        return f"Report for {title}"
    return f"{kind.capitalize()} report, {title} ({dates})"


def _next_period(kind, start):
    if kind == "weekly":
        return start + dt.timedelta(days=7)
    months = 1 if kind == "monthly" else 3
    m = start.month + months
    return start.replace(year=start.year + (m - 1) // 12, month=(m - 1) % 12 + 1)


def report_path(kind, start, end=None):
    stem = f"{kind}-{start:%Y-%m-%d}"
    if kind == "custom":
        stem += f"-to-{end - dt.timedelta(days=1):%Y-%m-%d}"
    return OUT / f"{stem}.pdf"


def due_reports(now=None):
    """Scheduled reports whose period has closed, it's past the send hour, and no
    PDF exists yet. Only periods ending after the scheduler first started count,
    so a fresh install doesn't back-fill old periods it has little data for."""
    st = load_settings()["schedule"]  # first: applies the time zone
    now = now or dt.datetime.now(TZ)
    if not st.get("enabled"):
        return []
    OUT.mkdir(parents=True, exist_ok=True)
    since_file = OUT / ".scheduler-since"
    if not since_file.exists():
        since_file.write_text(now.isoformat())
    since = dt.datetime.fromisoformat(since_file.read_text().strip())
    due = []
    for kind in st.get("kinds", []):
        if kind not in ("weekly", "monthly", "quarterly"):
            continue
        end = period_end(kind, now)
        if end > since and now.hour >= int(st.get("hour", 7)) and not report_path(kind, period_start(kind, end)).exists():
            due.append((kind, end))
    return due


def serve():
    """Scheduler only (no portal): check every 10 minutes."""
    log.info("scheduler up, output %s", OUT)
    while True:
        for kind, end in due_reports():
            try:
                run(kind, end)
            except Exception:  # noqa: BLE001 - retry on the next tick
                log.exception("%s report failed, retrying in 10 minutes", kind)
        time.sleep(600)


def main():
    load_settings()  # applies the configured time zone to command-line runs too
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("kind", choices=list(KINDS))
    r.add_argument("--end", help="last day to include, e.g. 2026-11-01; default: the last closed period")
    r.add_argument("--start", help="first day to include (custom reports only), e.g. 2026-11-01")
    r.add_argument("--no-email", action="store_true")
    sub.add_parser("serve")
    a = ap.parse_args()
    if a.cmd == "serve":
        serve()
    else:
        day = lambda x: dt.datetime.combine(dt.date.fromisoformat(x), dt.time(), TZ)
        if a.kind == "custom":
            if not a.start:
                ap.error("custom reports need --start (and optionally --end, default today)")
            end = day(a.end) + dt.timedelta(days=1) if a.end else dt.datetime.now(TZ)
            run("custom", end, send=False, start=day(a.start))
        else:
            # --end names the last day of the period; internally the end is exclusive
            end = day(a.end) + dt.timedelta(days=1) if a.end else None
            if end and end != period_end(a.kind, end):
                ap.error(f"--end for a {a.kind} report must be the last day of a {KINDS[a.kind]}")
            run(a.kind, end, send=not a.no_email)


if __name__ == "__main__":
    main()
