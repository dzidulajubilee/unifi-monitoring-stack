"""Made-up report data for rendering the real report in tests: every name and address here is invented
(documentation ranges such as 192.0.2.x and 198.51.100.x)."""

import datetime as dt

GB = 1e9
SIZES = {"empty": 0, "typical": 1, "huge": 4}


def _names(prefix, n, long=False):
    tail = " with a long name that keeps going" if long else ""
    return [f"{prefix} {i + 1}{tail if i % 3 == 0 else ''}" for i in range(n)]


def _top(names, start=900):
    return [{"name": nm, "n": float(start - i * 37)} for i, nm in enumerate(names)]


def collect(size, start, end):
    k = SIZES[size]
    long = size == "huge"
    days = max(1, (end - start).days)
    hours = [start + dt.timedelta(hours=h) for h in range(0, days * 24, 1)]
    aps = _names("Access point", 3 * k + (1 if k else 0), long)
    nets = _names("Network", 3 * k, long)
    d = {
        "wan_down": 412 * GB * k,
        "wan_up": 96 * GB * k,
        "wan_p95": 180e6 if k else None,
        "latency_avg": 12.5 if k else None,
        "latency_max": 88.0 if k else None,
        "wans": [{"name": f"Line {i + 1}", "uptime": 99.9 - i} for i in range(min(k, 3))],
        "failovers": float(2 * k),
        "cpu_avg": 0.21 if k else None,
        "cpu_max": 0.67 if k else None,
        "mem_start": 0.4 if k else None,
        "mem_end": 0.52 if k else None,
        "mem_max": 0.93 if size == "huge" else (0.6 if k else None),
        "temp_max": 61.0 if k else None,
        "gw_reboots": float(k),
        "clients_peak": float(120 * k),
        "clients_unique": float(180 * k),
        "clients_new": float(12 * k),
        "top_clients": [
            {"name": nm, "mac": f"02:00:00:00:00:{i:02x}", "detail": f"…00:{i:02x} · 192.0.2.{10 + i} · Network 1",
             "bytes": (50 - i * 4) * GB}
            for i, nm in enumerate(_names("Laptop", 10 if k else 0, long))
        ],
        "networks": [
            {"name": nm, "peak": float(60 - i * 4), "avg": float(30 - i * 2), "unique": float(70 - i * 3),
             "key": nm, "slot": i}
            for i, nm in enumerate(nets)
        ],
        "net_series": {nm: [(t.timestamp(), float(10 + (i * 7 + j) % 25)) for j, t in enumerate(hours)]
                       for i, nm in enumerate(nets)},
        "net_step": 3600,
        "aps": [
            {"name": nm, "peak": float(20 + i), "bytes": (30 - i) * GB, "sat": 0.9 - i * 0.02, "u24": 0.3,
             "u5": 0.1, "reboots": float(i % 4 if long else 0), "retries": 0.05 + (0.04 * i if long else 0),
             "signal": -60.0 - (3 * i if long else 0)}
            for i, nm in enumerate(aps)
        ],
        "blocks_in": float(30000 * k),
        "blocks_out": float(6000 * k),
        "block_sources": 9000 * k,
        "top_sources": _top([f"198.51.100.{i + 1}" for i in range(8 if k else 0)]),
        "top_policies": _top(_names("Policy", 8 if k else 0, long)),
        "top_ports": _top([f"{p}/TCP" for p in (22, 23, 445, 3389, 8080, 5900, 1433, 6379)][: 8 if k else 0]),
        "ports_since": start + dt.timedelta(days=2) if k else None,
        "top_devices": _top(_names("Phone", 8 if k else 0, long)),
        "threats": float(8 * k),
        "threats_high": float(6 * k),
        "threats_blocked": float(8 * k),
        "threats_open": 0.0,
        "threat_devices": _top(_names("Desktop", 3 if k else 0, long)),
        "fw_changes": float(5 * k),
        "logins": float(40 * k),
        "logins_outside": float(12 * k),
        "login_ips": _top([f"203.0.113.{i + 1}" for i in range(3 if k else 0)]),
        "own_ips": {"203.0.113.1"} if k else set(),
    }
    conflicts = [
        {"t": start + dt.timedelta(hours=5 + i * 7), "msg": f"Multiple devices are using the same 192.0.2.{50 + i} IP address.",
         "ip": f"192.0.2.{50 + i}", "network": "Network 1"}
        for i in range(4 * k)
    ]
    d["wifi"] = {
        "sat": 0.93 if k else None,
        "band5": 0.71 if k else None,
        "weak": [{"name": nm, "dbm": -78.0 - i, "ap": aps[0] if aps else "", "detail": f"…00:{i:02x}"}
                 for i, nm in enumerate(_names("Tablet", 6 if long else k))],
        "clients_wireless": 90 * k,
        "connects": float(1200 * k),
        "disconnects": float(1100 * k),
        "roams": float(2400 * k),
        "top_disconnects": _top(_names("Phone", 8 if k else 0, long)),
        "top_roams": _top(_names("Phone", 8 if k else 0, long)),
        "channel_changes": float(60 * k),
        "channel_by_ap": _top(aps[:8]),
        "ip_conflicts": conflicts,
        "ip_conflict_groups": [
            {"ip": c["ip"], "network": c["network"], "n": 1, "last": c["t"],
             "devices": [f"Acme device (…12:{i:02x})", f"Acme device (…34:{i:02x})"][: 1 + i % 2]}
            for i, c in enumerate(conflicts)
        ],
    }
    cats = _names("Category", 3 * k, long)
    chart_days = [start + dt.timedelta(days=i) for i in range(days)]
    shown = cats[:5] + (["Other"] if len(cats) > 5 else [])
    d["dpi"] = {
        "apps": [{"name": nm, "n": 0.3 / (i + 1)} for i, nm in enumerate(_names("App", 10 if k else 0, long))],
        "categories": [{"name": nm, "n": 0.4 / (i + 1)} for i, nm in enumerate(cats[:8])],
        "by_day": {
            "rows": [
                {"name": nm, "bytes": (80 - i * 5) * GB, "share": 0.4 / (i + 1), "per_day": (12 - i) * GB,
                 "per_day_before": (10 - i) * GB if i % 2 else None, "busiest": chart_days[i % days],
                 "busiest_bytes": (20 - i) * GB, "top_device": f"Laptop {i + 1}", "top_device_share": 0.4}
                for i, nm in enumerate(cats)
            ],
            "chart": [
                {"day": day, "partial": i == 1, "missing": False,
                 "parts": [(c, (5 + (i + j) % 7) * GB) for j, c in enumerate(shown)]}
                for i, day in enumerate(chart_days)
            ] if cats else [],
            "shown": shown,
            "partial_days": [chart_days[1]] if cats and days > 1 else [],
            "comparable": True,
            "spikes": [{"category": cats[0], "day": chart_days[-1], "bytes": 120 * GB, "times": 4.0,
                        "device": "Laptop 1", "device_share": 0.7}] if cats else [],
        },
        "collecting": bool(k),
        "coverage": 1.0 if k else 0.0,
        "p2p": 0.06 if long else 0.0,
        "p2p_devices": [{"name": "Laptop 2", "share": 0.6}] if long else [],
    }
    d["isp"] = {
        "lines": [
            {"name": f"Line {i + 1}", "wan": f"WAN{i + 1}", "state": "active" if i == 0 else "backup", "up": 99.6,
             "promised": 200.0 if i == 0 else None, "dl": 180.0, "dl_min": 90.0, "ul": 40.0, "lat": 12.0,
             "carried": 400 * GB, "outages": 3 * i, "down_s": 180 * i, "latency": i, "loss": 2 * i}
            for i in range(min(k, 3))
        ],
        "events": [
            {"t": start + dt.timedelta(hours=3 * i), "line": f"Line {1 + i % 2}", "evt": "NETWORK_WAN_FAILED",
             "msg": "Went down, traffic moved to Line 2" + (" after a long outage with a long description" if long else "")}
            for i in range(8 * k)
        ],
    }
    d["maint"] = {
        "updates": [{"name": "Switch 1", "model": "Switch", "version": "7.1.0"}] if long else [],
        "devices": 12 * k,
        "storage": [{"name": "Internal", "used": 20 * GB, "size": 120 * GB, "pct": 0.17}] if k else [],
        "pools": [{"network": nm, "peak": 0.85 if i == 0 else 0.4, "size": 250.0, "leases": 120.0}
                  for i, nm in enumerate(nets)],
        "sfp": [{"port": f"Port {i + 9}", "rx": -8.0 - i, "rx_start": None, "rx_min": -9.5, "temp": 45.0, "drop": None}
                for i in range(k)],
    }
    d["trends"] = trends(k) if k else None
    p = {key: d[key] for key in ("wan_down", "wan_up", "wan_p95", "clients_peak", "clients_new", "clients_unique",
                                 "blocks_in", "blocks_out", "threats_blocked", "threats", "networks")}
    p = dict(p, wan_down=d["wan_down"] * 0.9, clients_peak=d["clients_peak"] * 0.95)
    return d, p


def trends(k):
    labels = [f"W{40 + i}" for i in range(8)]
    keys = [("down", "Data downloaded", "bytes"), ("peak", "Peak devices online", "num"), ("wifi", "Wi-Fi experience", "pct"),
            ("blocks", "Blocked from the internet", "num"), ("threats", "Threats blocked", "num"),
            ("outages", "Internet outages", "num")]
    metrics = []
    for key, title, unit in keys:
        vals = [None, None] + [float(100 + i * 10) for i in range(6)]
        if unit == "pct":
            vals = [None, None] + [0.9 + i * 0.01 for i in range(6)]
        metrics.append({"key": key, "title": title, "unit": unit, "values": vals, "labels": labels,
                        "current": vals[-1], "avg": sum(vals[2:-1]) / 5, "n_before": 5})
    return {"metrics": metrics, "labels": labels, "errors": [], "word": "week"}


def dns_pair(size, start):
    """(this period, previous period) for one made-up DNS server."""
    k = SIZES[size]
    long = size == "huge"
    cur = {"name": "Resolver", "kind": "pihole", "url": "http://192.0.2.53", "version": "6.0", "error": None,
           "total": 300000 * k, "blocked": 150000 * k, "cached": 90000 * k, "forwarded": 60000 * k,
           "recursive": None, "failed": None, "nxdomain": None, "clients": 120 * k, "blocklist_domains": 900000,
           "blocklist_updated": start, "series": [],
           "top_domains": _top([f"cdn{i}.example.com" + (".with.a.very.long.subdomain.name" if long else "")
                                for i in range(10 if k else 0)]),
           "top_blocked": _top([f"ads{i}.example.net" for i in range(10 if k else 0)]),
           "top_clients": [{"name": f"Laptop {i + 1}", "ip": f"192.0.2.{20 + i}", "n": float(5000 - i * 300),
                            "detail": f"192.0.2.{20 + i}"} for i in range(10 if k else 0)],
           "upstreams": _top(["192.0.2.1#53", "198.51.100.53#53"][: 2 if k else 0]),
           "coverage": 1.0, "own": 10 * k}
    before = dict(cur, total=280000 * k, blocked=130000 * k)
    return cur, before
