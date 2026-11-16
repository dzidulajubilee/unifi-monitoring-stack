"""Gathers the figures for each report section from Prometheus and Loki."""

import datetime as dt
import json
import re

from . import config
from .config import LOKI
from .periods import trend_periods
from .sources import (
    AUDIT_FW,
    CLIENT_LOG,
    DEVICE_LOG,
    DPI_BOTH,
    DPI_RX,
    DPI_TX,
    FW_OUT,
    FW_SYSLOG_IN,
    IPS,
    LOGINS,
    PRIVATE,
    WAN_EVENTS,
    Source,
    _get,
)


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
    d["failovers"] = s.scalar(f"sum(count_over_time({WAN_EVENTS} |= `FAILED_OVER` [$R]))", "loki")

    # gateway
    gw = 'type="udm"'
    d["cpu_avg"] = s.scalar(f"avg(avg_over_time(unpoller_device_cpu_utilization_ratio{{{gw}}}[$R]))")
    d["cpu_max"] = s.scalar(f"max(max_over_time(unpoller_device_cpu_utilization_ratio{{{gw}}}[$R]))")
    d["mem_start"] = s.scalar(
        f"avg(avg_over_time(unpoller_device_memory_utilization_ratio{{{gw}}}[1d] offset {s.secs - 86400}s))"
    )
    d["mem_end"] = s.scalar(f"avg(avg_over_time(unpoller_device_memory_utilization_ratio{{{gw}}}[1d]))")
    d["mem_max"] = s.scalar(f"max(max_over_time(unpoller_device_memory_utilization_ratio{{{gw}}}[$R]))")
    d["temp_max"] = s.scalar(f'max(max_over_time(unpoller_device_temperature_celsius{{{gw},temp_area="CPU"}}[$R]))')
    d["gw_reboots"] = s.scalar(f"sum(resets(unpoller_device_uptime_seconds{{{gw}}}[$R]))")

    # clients
    clients = "unpoller_client_uptime_seconds"
    d["clients_peak"] = s.scalar(f"max_over_time(count({clients})[$R:5m])")
    d["clients_unique"] = s.scalar(f"count(count by (mac) (last_over_time({clients}[$R])))")
    d["clients_new"] = s.scalar(
        f"count(count by (mac) (last_over_time({clients}[$R])) "
        f"unless count by (mac) (last_over_time({clients}[$R] offset $R)))"
    )
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

    # devices by network, as in Grafana's Clients dashboard: the most online at once, the average, and how many
    # different devices used each network ("" is Wi-Fi devices caught before they got an address)
    per_net = f"count by (network) (count by (network, mac) ({clients}))"  # each device once, even with two addresses
    peak = s.by(f"max_over_time({per_net}[$R:5m])", "network")
    avg = s.by(f"avg_over_time({per_net}[$R:5m])", "network")
    seen = s.by(f"count by (network) (count by (network, mac) (last_over_time({clients}[$R])))", "network")
    # devices without a network (Wi-Fi devices caught before they got an address) are left out
    order = sorted(set(peak) | set(seen))
    d["networks"] = sorted(
        ({"name": n, "peak": peak.get(n), "avg": avg.get(n), "unique": seen.get(n), "key": n} for n in order if n),
        key=lambda x: (-(x["peak"] or 0), x["name"]),
    )
    # the chart: devices per network, averaged per hour (a week), 6 hours (a month) or day (longer)
    step = 3600 if s.secs <= 10 * 86400 else 21600 if s.secs <= 45 * 86400 else 86400
    rows = s.prom_range(f"avg_over_time({per_net}[{step}s:5m])", step) or []
    d["net_series"] = {x["metric"].get("network", ""): [(float(t), float(v)) for t, v in x["values"]] for x in rows}
    d["net_step"] = step

    # access points, one row per device (by MAC) under its latest name: an access point renamed during the period
    # (a new one adopted as "U6 Pro" and then named for its room, say) is one row, not one per name
    names, latest = device_names(s, "uap")
    aps = s.by("max_over_time(sum by (mac) (unpoller_device_radio_stations)[$R:5m])", "mac")
    # these two only carry the name, so the totals under each of a device's names are added up
    traffic = _by_device(s.by('sum by (name) (increase(unpoller_device_bytes_total{type="uap"}[$R]))', "name"), names)
    reb = _by_device(s.by('sum by (name) (resets(unpoller_device_uptime_seconds{type="uap"}[$R]))', "name"), names)
    sat = s.by("avg_over_time(avg by (mac) (unpoller_device_vap_satisfaction_ratio >= 0)[$R:5m])", "mac")
    util = (
        s.prom("avg by (mac, band) (avg_over_time(unpoller_device_radio_channel_utilization_total_ratio[$R]))") or []
    )
    u = {}
    for x in util:
        u.setdefault(x["metric"].get("mac", ""), {})[x["metric"].get("band")] = float(x["value"][1])
    retries = s.by(
        # resends as a share of everything sent (0-100%); UniFi counts every resend attempt, so dividing by
        # delivered packets alone can pass 100% on a quiet access point
        "sum by (mac) (increase(unpoller_device_vap_transmit_retries_total[$R])) / "
        "(sum by (mac) (increase(unpoller_device_vap_transmit_packets_total[$R])) + "
        "sum by (mac) (increase(unpoller_device_vap_transmit_retries_total[$R])))",
        "mac",
    )
    signal = s.by("avg_over_time(avg by (mac) (unpoller_device_vap_average_client_signal < 0)[$R:5m])", "mac")
    d["aps"] = sorted(
        (
            {
                "name": latest.get(m, m),
                "peak": aps[m],
                "bytes": traffic.get(m),
                "sat": sat.get(m),
                "u24": u.get(m, {}).get("2.4"),
                "u5": u.get(m, {}).get("5"),
                "reboots": reb.get(m, 0),
                "retries": retries.get(m),
                "signal": signal.get(m),
            }
            for m in aps
        ),
        key=lambda a: -(a["bytes"] or 0),
    )

    # security
    L = "loki"
    # blocks of internet traffic: the System Log, then UniFi's firewall log over syslog once it's set up
    d["blocks_in"] = _total(s.internet(lambda sel: f"sum(count_over_time({sel} [$R]))"))
    d["blocks_out"] = s.scalar(f"sum(count_over_time({FW_OUT} [$R]))", L)
    sources = _sum_by(s.internet(lambda sel: f"sum by (src) (count_over_time({sel} [$R]))"), "src")
    d["block_sources"] = None if sources is None else len(sources)
    d["top_sources"] = _top(sources or {})[:8]
    policies = _sum_by(s.loki(f"sum by (policy) (count_over_time({FW_OUT} [$R]))"), "policy") or {}
    for k, v in (_sum_by(s.internet(lambda sel: f"sum by (policy) (count_over_time({sel} [$R]))"), "policy") or {}).items():
        policies[k] = policies.get(k, 0) + v
    d["top_policies"] = _top(policies)[:8]
    # ports only come with the firewall log over syslog
    t0 = s.syslog_start()
    ports = (
        _sum_by(s.loki_at(f'sum by (dpt, proto) (count_over_time({FW_SYSLOG_IN} | dpt != "" [$R]))', t0 - 1,
                          s.end.timestamp()), ("dpt", "proto"))
        if t0 else None
    )
    d["top_ports"] = _top({f"{k[0]}/{k[1]}": v for k, v in (ports or {}).items()})[:8]
    d["ports_since"] = dt.datetime.fromtimestamp(t0, config.TZ) if t0 else None
    d["top_devices"] = _top(s.by(f"topk(8, sum by (src) (count_over_time({FW_OUT} [$R])))", "src", L))
    d["threats"] = s.scalar(f"sum(count_over_time({IPS} [$R]))", L)
    d["threats_high"] = s.scalar(f'sum(count_over_time({IPS} | sev=~"HIGH|VERY_HIGH" [$R]))', L)
    d["threats_blocked"] = s.scalar(f'sum(count_over_time({IPS} | evt=~"(?i).*block.*" [$R]))', L)
    d["threats_open"] = (
        (d["threats"] - d["threats_blocked"]) if None not in (d["threats"], d["threats_blocked"]) else None
    )
    d["threat_devices"] = _top(s.by(f"topk(8, sum by (src) (count_over_time({IPS} [$R])))", "src", L))
    d["fw_changes"] = s.scalar(f"sum(count_over_time({AUDIT_FW} [$R]))", L)
    d["logins"] = s.scalar(f"sum(count_over_time({LOGINS} [$R]))", L)
    d["logins_outside"] = s.scalar(f"sum(count_over_time({LOGINS} | ip !~ `{PRIVATE}` [$R]))", L)
    d["login_ips"] = _top(s.by(f"topk(3, sum by (ip) (count_over_time({LOGINS} | ip !~ `{PRIVATE}` [$R])))", "ip", L))
    # the gateway's own internet addresses: logins "from outside" via these come through your own lines
    d["own_ips"] = set(
        s.by("group by (port_ip) (last_over_time(unpoller_device_port_port_speed_bps[$R]))", "port_ip")
    ) | set(s.by("group by (port_ip) (last_over_time(unpoller_device_port_sfp_rx_power[$R]))", "port_ip"))
    return d


WEAK_DBM = -75  # below this, Wi-Fi is slow and drops; UniFi also treats it as poor


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
    info = {
        x["metric"].get("mac"): x["metric"] for x in s.prom("last_over_time(unpoller_client_radio_signal_db[$R])") or []
    }
    weak = []
    for mac, dbm in sig.items():
        if dbm < WEAK_DBM:
            m = info.get(mac, {})
            name = m.get("name") or mac
            if re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", name):
                name = "Unnamed device"
            weak.append(
                {
                    "name": name,
                    "dbm": dbm,
                    "ap": m.get("ap_name", ""),
                    "detail": " · ".join(x for x in (mac[-5:], m.get("ap_name", ""), m.get("essid", "")) if x),
                }
            )
    d["weak"] = sorted(weak, key=lambda w: w["dbm"])
    d["clients_wireless"] = len(sig)

    ev = lambda e: f'{CLIENT_LOG} |= `"event":"{e}"`'
    d["connects"] = s.scalar(f"sum(count_over_time({ev('CLIENT_CONNECTED_WIRELESS')} [$R]))", L)
    d["disconnects"] = s.scalar(f"sum(count_over_time({ev('CLIENT_DISCONNECTED_WIRELESS')} [$R]))", L)
    d["roams"] = s.scalar(f"sum(count_over_time({ev('CLIENT_ROAMED')} [$R]))", L)
    who = '| json client="parameters.CLIENT.name"'
    d["top_disconnects"] = _top(
        s.by(
            f"topk(8, sum by (client) (count_over_time({ev('CLIENT_DISCONNECTED_WIRELESS')} {who} [$R])))", "client", L
        )
    )
    d["top_roams"] = _top(
        s.by(f"topk(8, sum by (client) (count_over_time({ev('CLIENT_ROAMED')} {who} [$R])))", "client", L)
    )
    chan = f'{DEVICE_LOG} |= `"event":"AP_CHANGED_CHANNELS"` | json dev="parameters.DEVICE.name"'
    d["channel_changes"] = s.scalar(f"sum(count_over_time({chan} [$R]))", L)
    # the log names the access point as it was called then: count a renamed one under its latest name
    names, latest = device_names(s, "uap")
    by_dev = _by_device(s.by(f"sum by (dev) (count_over_time({chan} [$R]))", "dev", L), names)
    d["channel_by_ap"] = _top({latest.get(k, k): v for k, v in by_dev.items()})
    conflicts = []
    for stream in s.loki_lines(f'{DEVICE_LOG} |= `"event":"CLIENT_IP_CONFLICT"`', 50) or []:
        for ts, line in stream["values"]:
            j = _json(line)
            par = j.get("parameters") or {}
            conflicts.append(
                {
                    "t": dt.datetime.fromtimestamp(int(ts) / 1e9, config.TZ),
                    "msg": j.get("message", ""),
                    "ip": (par.get("IP") or {}).get("name", ""),
                    "network": (par.get("NETWORK") or {}).get("name", ""),
                }
            )
    d["ip_conflicts"] = sorted(conflicts, key=lambda c: c["t"])
    d["ip_conflict_groups"] = conflict_groups(s, d["ip_conflicts"])
    return d


def conflict_groups(s: Source, conflicts):
    """One row per conflicting address: how often, when last, and the devices UniFi saw with that address
    in the hour around each conflict. UniFi's conflict entries don't name the devices, so they come from
    the client metrics; a device with a fixed address that UniFi doesn't list can't be named."""
    groups = {}
    for c in conflicts:
        if not c["ip"]:
            continue
        g = groups.setdefault(c["ip"], {"ip": c["ip"], "network": c["network"], "n": 0, "last": c["t"], "devices": {}})
        g["n"] += 1
        g["last"] = max(g["last"], c["t"])
        q = f'max by (mac, name, hostname, oui) (max_over_time(unpoller_client_uptime_seconds{{ip="{c["ip"]}"}}[2h]))'
        for x in s.prom_at(q, c["t"].timestamp() + 3600) or []:
            m = x["metric"]
            g["devices"][m.get("mac", "")] = _device_name(m)
    out = sorted(groups.values(), key=lambda g: (-g["n"], g["ip"]))
    for g in out:
        g["devices"] = [g["devices"][k] for k in sorted(g["devices"])]
    return out


def _device_name(m):
    """'Acme device (…12:34)' for a device UniFi only knows by its MAC, else 'name (…12:34)'."""
    mac = m.get("mac", "")
    name = m.get("name") or m.get("hostname") or ""
    if not name or re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", name):
        oui = re.split(r",| Inc\b| Ltd\b| Co\.| Corp| Digital| Technolog| Systems", m.get("oui") or "")[0].strip()
        oui = re.sub(r"^(Hangzhou|Shenzhen|Guangdong|Beijing|Shanghai|Zhejiang|Xiamen|Fujian|Qingdao) ", "", oui)
        name = f"{oui} device" if oui else "Unnamed device"
    return f"{name} (…{mac[-5:]})" if mac else name


def collect_apps(s: Source, prev: Source = None):
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
        return (
            [{"name": k, "n": v / total} for k, v in sorted(out.items(), key=lambda kv: -kv[1]) if v > 0]
            if total
            else []
        )

    d = {"apps": share("application")[:10], "categories": share("category")[:8]}
    d["by_day"] = category_summary(category_days(s), category_days(prev) if prev else None, names=known_devices(s))
    d["collecting"] = bool(s.scalar(f"count(last_over_time({DPI_RX}[$R]))"))
    have = s.scalar(f"count_over_time(count({DPI_RX})[$R:{step}])")
    d["coverage"] = min(1.0, (have or 0) / max(1, s.secs // int(step[:-1])))
    p2p = sum(r["n"] for r in d["categories"] if "peer" in r["name"].lower())
    d["p2p"] = p2p or sum(r["n"] for r in d["apps"] if "torrent" in r["name"].lower())
    # which devices the peer-to-peer traffic came from, as a share of it
    who = {}
    for m in (DPI_RX, DPI_TX):
        sel = m.replace("}", ', category=~"Peer-to-Peer.*"}')
        vals = (
            s.prom(f"avg_over_time(sum by (name, mac) ({sel})[$R:{step}])")
            or s.prom(f"sum by (name, mac) (sum_over_time({sel}[$R]))")
            or []
        )
        for x in vals:
            k = (x["metric"].get("name", ""), x["metric"].get("mac", ""))
            who[k] = who.get(k, 0) + float(x["value"][1])
    tot = sum(who.values())
    d["p2p_devices"] = (
        [
            {"name": device_label(n, mac), "share": v / tot}
            for (n, mac), v in sorted(who.items(), key=lambda kv: -kv[1])[:3]
        ]
        if tot
        else []
    )
    return d


def _category(k):
    """UniFi's category label without its number: 'Network Protocols_20' -> 'Network Protocols'."""
    return re.sub(r"_\d*$", "", k or "").replace("_", " ").strip() or "Unknown"


def category_days(s: Source):
    """Traffic per category for each day of the period, in bytes, with the devices behind it.

    UniFi's application figures are rolling 24-hour totals, so the reading at midnight is that day's traffic
    (as on the Applications dashboard). A day is marked incomplete when the figure fell to almost nothing
    during it, which happens when UniFi restarts (an update, say) and starts counting again."""
    days = []
    day = s.start
    while day + dt.timedelta(days=1) <= s.end + dt.timedelta(minutes=1):
        t = (day + dt.timedelta(days=1)).timestamp()
        res = s.prom_at(f"sum by (category, name, mac) ({DPI_BOTH})", t)
        cats, devices = {}, {}
        for x in res or []:
            m, v = x["metric"], float(x["value"][1])
            c = _category(m.get("category"))
            cats[c] = cats.get(c, 0) + v
            key = (m.get("name", ""), m.get("mac", ""))
            devices.setdefault(c, {})[key] = devices.setdefault(c, {}).get(key, 0) + v
        low = s.prom_at(f"min_over_time(sum({DPI_BOTH})[1d:10m])", t)
        high = s.prom_at(f"max_over_time(sum({DPI_BOTH})[1d:10m])", t)
        lo = float(low[0]["value"][1]) if low else 0.0
        hi = float(high[0]["value"][1]) if high else 0.0
        days.append(
            {
                "day": day,
                "cats": cats,
                "devices": devices,
                "total": sum(cats.values()),
                "missing": not cats,
                "partial": bool(cats) and hi > 0 and lo < 0.1 * hi,
            }
        )
        day += dt.timedelta(days=1)
    return days


def known_devices(s: Source):
    """mac -> a readable label for every device UniFi listed during the period."""
    out = {}
    for x in s.prom("last_over_time(unpoller_client_uptime_seconds[$R])") or []:
        m = x["metric"]
        if m.get("mac"):
            out[m["mac"]] = _device_name(m)
    return out


def category_summary(days, before, top=5, names=None):
    """Per-category totals, the busiest day and the device that used most, with the change per complete day
    against the earlier period; the chart's rows (top categories plus Other) per day."""
    good = [x for x in days if not x["missing"] and not x["partial"]]
    good_before = [x for x in before or [] if not x["missing"] and not x["partial"]]
    totals, per_dev = {}, {}
    for x in days:
        if x["missing"]:
            continue
        for c, v in x["cats"].items():
            totals[c] = totals.get(c, 0) + v
            for k, b in x["devices"].get(c, {}).items():
                per_dev.setdefault(c, {})[k] = per_dev.setdefault(c, {}).get(k, 0) + b
    grand = sum(totals.values())
    comparable = len(days) and len(good_before) >= 0.7 * len(before or []) and len(good) >= 0.7 * len(days)
    rows = []
    for c, v in sorted(totals.items(), key=lambda kv: -kv[1]):
        busiest = max((x for x in days if not x["missing"]), key=lambda x: x["cats"].get(c, 0))
        devs = sorted(per_dev.get(c, {}).items(), key=lambda kv: -kv[1])
        avg = sum(x["cats"].get(c, 0) for x in good) / len(good) if good else None
        avg_before = (
            sum(x["cats"].get(c, 0) for x in good_before) / len(good_before) if comparable and good_before else None
        )
        rows.append(
            {
                "name": c,
                "bytes": v,
                "share": v / grand if grand else None,
                "per_day": avg,
                "per_day_before": avg_before,
                "busiest": busiest["day"],
                "busiest_bytes": busiest["cats"].get(c, 0),
                "top_device": _label(devs[0][0], names) if devs else None,
                "top_device_share": devs[0][1] / v if devs and v else None,
            }
        )
    shown = [r["name"] for r in rows[:top]]
    chart = [
        {
            "day": x["day"],
            "partial": x["partial"],
            "missing": x["missing"],
            "parts": [(c, x["cats"].get(c, 0)) for c in shown]
            + [("Other", sum(v for c, v in x["cats"].items() if c not in shown))],
        }
        for x in days
    ]
    return {
        "rows": rows,
        "chart": chart,
        "shown": shown + (["Other"] if len(rows) > top else []),
        "partial_days": [x["day"] for x in days if x["partial"] or x["missing"]],
        "comparable": bool(comparable),
        "spikes": _spikes(days, rows, names),
    }


def _label(key, names):
    name, mac = key
    return (names or {}).get(mac) or device_label(name, mac)


def _spikes(days, rows, names=None):
    """Single days where a category ran far above its usual day: at least 3x the median of its other complete
    days and over 20 GB, with the device that used most of it that day."""
    good = [x for x in days if not x["missing"] and not x["partial"]]
    out = []
    for r in rows[:8]:
        c = r["name"]
        for x in good:
            v = x["cats"].get(c, 0)
            others = sorted(y["cats"].get(c, 0) for y in good if y is not x)
            if len(others) < 3 or v < 20e9:
                continue
            usual = others[len(others) // 2]
            if usual and v >= 3 * usual:
                devs = sorted(x["devices"].get(c, {}).items(), key=lambda kv: -kv[1])
                out.append(
                    {
                        "category": c,
                        "day": x["day"],
                        "bytes": v,
                        "times": v / usual,
                        "device": _label(devs[0][0], names) if devs else None,
                        "device_share": devs[0][1] / v if devs else None,
                    }
                )
    return sorted(out, key=lambda s: -s["bytes"])[:3]


def client_names(s: Source):
    """ip -> (device name, mac) for every address only one device used during the period, or
    ip -> None for an address several devices used. DNS servers only know addresses, and leases move
    addresses between devices, so a name is only given when it can't be someone else's."""
    seen = {}
    for x in s.prom("last_over_time(unpoller_client_uptime_seconds[$R])") or []:
        m = x["metric"]
        if not m.get("ip") or not m.get("mac"):
            continue
        name = m.get("name") or ""
        if not name or re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", name):
            name = f"{m['oui']} device" if m.get("oui") else "Unnamed device"
        seen.setdefault(m["ip"], {})[m["mac"]] = name
    return {ip: next(iter(d.items()))[::-1] if len(d) == 1 else None for ip, d in seen.items()}


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
    for x in (
        s.prom("max by (wan_interface, wan_networkgroup, state) (last_over_time(unpoller_wan_interface_state[$R]))")
        or []
    ):
        m = x["metric"]
        state[m.get("wan_networkgroup")] = {
            "name": m.get("wan_interface"),
            "state": (m.get("state") or "").capitalize(),
        }
    up = by("avg_over_time(max by (wan_networkgroup) (unpoller_wan_uptime_percentage >= 0)[$R:5m])", "wan_networkgroup")
    promised = by(
        "max by (wan_networkgroup) (last_over_time(unpoller_wan_provider_download_kbps[$R]))", "wan_networkgroup"
    )
    dl = by("avg_over_time(max by (wan_group) (unpoller_speedtest_download_mbps)[$R:15m])", "wan_group")
    dl_min = by("min_over_time(max by (wan_group) (unpoller_speedtest_download_mbps)[$R:15m])", "wan_group")
    ul = by("avg_over_time(max by (wan_group) (unpoller_speedtest_upload_mbps)[$R:15m])", "wan_group")
    lat = by("avg_over_time(max by (wan_group) (unpoller_speedtest_latency_ms)[$R:15m])", "wan_group")
    carried = by(
        "sum by (port_name) (increase(unpoller_device_port_receive_bytes_total[$R]) + "
        "increase(unpoller_device_port_transmit_bytes_total[$R]))",
        "port_name",
    )
    lines = {}
    for wan, st in state.items():
        lines[st["name"]] = {
            "name": st["name"],
            "wan": wan,
            "state": st["state"],
            "up": up.get(wan),
            "promised": (promised.get(wan) or 0) / 1000 or None,
            "dl": dl.get(wan),
            "dl_min": dl_min.get(wan),
            "ul": ul.get(wan),
            "lat": lat.get(wan),
            "carried": carried.get(st["name"]),
            "outages": 0,
            "down_s": 0,
            "latency": 0,
            "loss": 0,
        }

    events = []
    for stream in s.loki_lines(WAN_EVENTS, 1000) or []:
        for ts, raw in stream["values"]:
            j = _json(raw)
            evt = j.get("event", "")
            if evt not in ISP_EVENTS:
                continue
            prm = j.get("parameters", {})

            def val(k, f="name", prm=prm):
                return (prm.get(k) or {}).get(f) or ""

            line = val("WAN_NAME") or val("WAN_NAME", "id")
            secs = int(val("DURATION", "id") or 0)
            text = ISP_EVENTS[evt].format(
                to=val("FAILOVER_WAN_NAME") or "a backup line",
                dur=val("DURATION") or "a while",
                lat=f" ({val('LATENCY')} ms)" if val("LATENCY") else "",
                count=val("COUNT") or "several",
            )
            events.append(
                {"t": dt.datetime.fromtimestamp(int(ts) / 1e9, config.TZ), "line": line, "msg": text, "evt": evt}
            )
            ln = lines.setdefault(
                line,
                {
                    "name": line,
                    "wan": "",
                    "state": "",
                    "up": None,
                    "promised": None,
                    "dl": None,
                    "dl_min": None,
                    "ul": None,
                    "lat": None,
                    "carried": None,
                    "outages": 0,
                    "down_s": 0,
                    "latency": 0,
                    "loss": 0,
                },
            )
            if evt in ("NETWORK_FAILED_OVER_TO_BACKUP_WAN", "NETWORK_FAILED_OVER_TO_BACKUP_WAN_TEMPORARY"):
                ln["outages"] += 1
            if evt in ("NETWORK_WAN_RESTORED", "NETWORK_FAILED_OVER_TO_BACKUP_WAN_TEMPORARY"):
                ln["down_s"] += secs  # each outage ends with one of these, carrying its length
            if evt == "ISP_HIGH_LATENCY":
                ln["latency"] += 1
            if evt == "ISP_PACKET_LOSS":
                ln["loss"] += 1
    return {
        "lines": sorted(lines.values(), key=lambda l: l["wan"] or "~"),
        "events": sorted(events, key=lambda e: e["t"]),
    }


TRENDS = [  # key, title, unit ("bytes", "num", "pct"), source
    ("down", "Data downloaded", "bytes", "prom"),
    ("peak", "Peak devices online", "num", "prom"),
    ("wifi", "Wi-Fi experience", "pct", "prom"),
    ("blocks", "Blocked from the internet", "num", "loki"),
    ("threats", "Threats blocked", "num", "loki"),
    ("outages", "Internet outages", "num", "loki"),
]


def _trend_point(start, end):
    """Key figures for one period; None where there isn't enough data to say."""
    s = Source(start, end)
    # share of the period Prometheus has UnPoller data for, and whether Loki has any logs at all
    prom = (s.scalar('sum(count_over_time(up{job="unpoller"}[$R]))') or 0) / max(1, s.secs / 60)
    days = max(1, round(s.secs / 86400))
    per_day = (
        s._safe(
            lambda q: _get(
                LOKI + "/loki/api/v1/query_range",
                {
                    "query": q,
                    "start": int((start.timestamp() + 86400) * 1e9),
                    "end": int(end.timestamp() * 1e9),
                    "step": "86400",
                },
            ),
            'sum(count_over_time({application="unifi_system_log"}[1d]))',
        )
        or []
    )
    loki_days = sum(1 for _, n in (per_day[0]["values"] if per_day else []) if float(n) > 0)
    # counts only mean something for periods logged (almost) throughout; partial ones would look tiny
    loki = loki_days / days >= 0.9
    v = {"_prom": prom}
    ok = prom >= 0.5
    # same query as the report's own Downloaded figure, so the numbers match
    v["down"] = (
        s.scalar(f'avg_over_time(max(unpoller_site_receive_rate_bytes{{subsystem="wan"}})[$R:1m]) * {s.secs}')
        if ok
        else None
    )
    v["peak"] = s.scalar("max_over_time(count(unpoller_client_uptime_seconds)[$R:15m])") if prom > 0 else None
    v["wifi"] = (
        s.scalar('avg_over_time(avg(unpoller_client_satisfaction_ratio{wired="false"} >= 0)[$R:15m])') if ok else None
    )
    if loki:
        v["blocks"] = _total(s.internet(lambda sel: f"sum(count_over_time({sel} [$R]))"))
        v["threats"] = s.scalar(f'sum(count_over_time({IPS} | evt=~"(?i).*block.*" [$R]))', "loki")
        v["outages"] = s.scalar(f"sum(count_over_time({WAN_EVENTS} |= `FAILED_OVER` [$R]))", "loki")
    else:
        v["blocks"] = v["threats"] = v["outages"] = None
    return v, s.errors


def collect_trends(kind, start):
    """Each key figure across the report's period and the ones before it."""
    from concurrent.futures import ThreadPoolExecutor

    periods = trend_periods(kind, start)
    if not periods:
        return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        points = list(pool.map(lambda p_: _trend_point(p_[0], p_[1]), periods))
    errors = [e for _, errs in points for e in errs]
    labels = [lbl for _, _, lbl in periods]
    out = []
    for key, title, unit, _src in TRENDS:
        vals = [pt[key] for pt, _ in points]
        cur, before = vals[-1], [x for x in vals[:-1] if x is not None]
        avg = sum(before) / len(before) if before else None
        out.append(
            {
                "key": key,
                "title": title,
                "unit": unit,
                "values": vals,
                "labels": labels,
                "current": cur,
                "avg": avg,
                "n_before": len(before),
            }
        )
    return {
        "metrics": out,
        "labels": labels,
        "errors": errors,
        "word": {"weekly": "weeks", "monthly": "months", "quarterly": "quarters"}[kind],
    }


def collect_maint(s: Source):
    """Housekeeping: access point and switch firmware, console storage, address pools and fibre modules."""
    # Not reported: automatic backups, UniFi Network / UniFi OS updates and the console's own firmware.
    # On UniFi OS consoles UnPoller can't read those (its controller_* metrics stay at 0, uptime included),
    # so they'd say "off" / "up to date" whatever the truth is. Device firmware for access points and
    # switches comes from each device's own status and is reliable.
    d = {}
    ver = {
        x["metric"].get("name"): x["metric"]
        for x in s.prom(f'last_over_time(unpoller_device_info{{type!~"{CONSOLES}"}}[$R])') or []
    }
    d["updates"] = [
        {"name": n, "model": ver.get(n, {}).get("model", ""), "version": ver.get(n, {}).get("version", "")}
        for n, v in sorted(
            s.by(
                f'max by (name) (last_over_time(unpoller_device_upgradable{{type!~"{CONSOLES}"}}[$R]))', "name"
            ).items()
        )
        if v >= 1
    ]
    d["devices"] = len(ver)
    size = s.by(
        'max by (name, storage_name) (last_over_time(unpoller_device_storage{storage_reading="size"}[$R]))',
        "storage_name",
    )
    used = s.by(
        'max by (name, storage_name) (last_over_time(unpoller_device_storage{storage_reading="used"}[$R]))',
        "storage_name",
    )
    d["storage"] = [
        {"name": k, "used": used.get(k, 0), "size": v, "pct": used.get(k, 0) / v if v else None}
        for k, v in sorted(size.items())
    ]
    peak = s.by("max by (network) (max_over_time(unpoller_dhcp_utilization_percent[$R]))", "network")
    pool = s.by("max by (network) (last_over_time(unpoller_dhcp_pool_size[$R]))", "network")
    leases = s.by("max by (network) (max_over_time(unpoller_dhcp_active_leases[$R]))", "network")
    d["pools"] = sorted(
        ({"network": n, "peak": v / 100, "size": pool.get(n), "leases": leases.get(n)} for n, v in peak.items()),
        key=lambda x: -x["peak"],
    )
    first = s.secs - 86400
    rx_end = s.by("avg by (port_name) (avg_over_time(unpoller_device_port_sfp_rx_power[1d]))", "port_name")
    rx_start = (
        s.by(f"avg by (port_name) (avg_over_time(unpoller_device_port_sfp_rx_power[1d] offset {first}s))", "port_name")
        if first > 0
        else {}
    )
    rx_min = s.by("min by (port_name) (min_over_time(unpoller_device_port_sfp_rx_power[$R]))", "port_name")
    temp = s.by("max by (port_name) (max_over_time(unpoller_device_port_sfp_temperature[$R]))", "port_name")
    d["sfp"] = [
        {
            "port": p,
            "rx": v,
            "rx_start": rx_start.get(p),
            "rx_min": rx_min.get(p),
            "temp": temp.get(p),
            "drop": (rx_start[p] - v) if p in rx_start else None,
        }
        for p, v in sorted(rx_end.items())
    ]
    return d


CONSOLES = "udm|ucg|udr|uxg|usg|ugw|uxgpro"  # gateways / consoles: firmware state not readable, see collect_maint


SFP_WEAK_DBM = -20  # most 1G single-mode modules stop working around -20 to -24 dBm


SFP_DROP_DB = 2  # a fall this large over a period means the fibre or a connector is degrading


def device_names(s: Source, kind):
    """Every name each UniFi device of this type had during the period: ({name: mac}, {mac: latest name})."""
    rows = s.prom(
        f'max by (mac, name) (max_over_time(timestamp(unpoller_device_info{{type="{kind}"}})[$R:5m]))'
    ) or []
    names, seen = {}, {}
    for x in rows:
        mac, name = x["metric"].get("mac"), x["metric"].get("name")
        if not mac or not name:
            continue
        names[name] = mac
        t = float(x["value"][1])
        if t >= seen.get(mac, (0.0, ""))[0]:
            seen[mac] = (t, name)
    return names, {mac: name for mac, (_, name) in seen.items()}


def _by_device(by_name, names):
    """{name: value} -> {mac: value}, adding up a device's values under its old and new names; names without a
    known device keep their own row."""
    out = {}
    for name, v in by_name.items():
        key = names.get(name, name)
        out[key] = out.get(key, 0) + v
    return out


def _total(rows):
    """Sum of a list of Loki metric rows; None when a query failed."""
    return None if rows is None else sum(float(x["value"][1]) for x in rows)


def _sum_by(rows, label):
    """{label value: total} over Loki metric rows (a tuple of labels gives tuple keys); None when a query failed."""
    if rows is None:
        return None
    out = {}
    for x in rows:
        m = x["metric"]
        k = tuple(m.get(lb, "") for lb in label) if isinstance(label, tuple) else m.get(label, "")
        out[k] = out.get(k, 0) + float(x["value"][1])
    return out


def _top(m):
    """Largest first; ties by name, so a report reads the same every time it's made."""
    return [{"name": k or "unknown", "n": v} for k, v in sorted(m.items(), key=lambda kv: (-kv[1], kv[0]))]


def events(s: Source, wan=True):
    """Notable events: each internet failover, and firewall changes grouped per admin per day."""
    out, changes = [], {}
    for stream in (s.loki_lines(f"{WAN_EVENTS} |= `FAILED_OVER`", 200) if wan else None) or []:
        for ts, line in stream["values"]:
            t = dt.datetime.fromtimestamp(int(ts) / 1e9, config.TZ)
            out.append({"t": t, "kind": "Internet", "msg": _json(line).get("message", line)})
    for stream in s.loki_lines(AUDIT_FW, 2000) or []:
        for ts, line in stream["values"]:
            t = dt.datetime.fromtimestamp(int(ts) / 1e9, config.TZ)
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
        out.append(
            {
                "t": g["t"],
                "kind": "Firewall change",
                "msg": f"{admin} made {g['n']} change{'s' * (g['n'] != 1)} to {what}" + (f": {objs}" if objs else ""),
            }
        )
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

            def name(k, prm=prm):
                return prm.get(k, {}).get("name") or prm.get(k, {}).get("id") or ""

            rows.append(
                {
                    "t": dt.datetime.fromtimestamp(int(ts) / 1e9, config.TZ),
                    "src": name("SRC_CLIENT") or name("SRC_IP"),
                    "dst": name("DST_CLIENT") or name("DST_IP") or name("DST_DEVICE"),
                    "sev": (j.get("severity") or "").replace("_", " ").capitalize(),
                    "blocked": "BLOCK" in (j.get("event") or "").upper(),
                }
            )
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
