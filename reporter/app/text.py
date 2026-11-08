"""Number formatting and the plain-language summary and needs-attention lines."""

import datetime as dt
import re

from .collect import SFP_DROP_DB, SFP_WEAK_DBM, WEAK_DBM
from .config import SECTIONS


def fduration(secs):
    secs = int(secs or 0)
    if secs < 60:
        return f"{secs} s"
    if secs < 3600:
        return f"{secs // 60} min"
    h, m = divmod(secs // 60, 60)
    return f"{h} h {m} min" if m else f"{h} h"


def trend_change(m, plural):
    """'+12% vs the 7-week average' / 'first period with data'."""
    cur, avg, n = m["current"], m["avg"], m["n_before"]
    one = plural[:-1]
    if cur is None:
        return f"not enough data for this {one}"
    if not n or avg is None:
        return "no earlier data to compare"
    vs = f"vs the {n}-{one} average" if n > 1 else f"vs the previous {one}"
    if m["unit"] == "pct":
        diff = (cur - avg) * 100
        return (
            f"same as the {n}-{one} average"
            if n > 1 and abs(diff) < 0.5
            else "same as the previous " + one
            if abs(diff) < 0.5
            else f"{diff:+.0f} points {vs}"
        )
    if avg < 20:  # small counts read better as numbers than percentages
        base = f"{avg:.1f}".rstrip("0").rstrip(".") if n > 1 else f"{avg:.0f}"
        return f"{cur:.0f} vs an average of {base}" if n > 1 else f"{cur:.0f} vs {base} the {one} before"
    pct = (cur - avg) / avg * 100
    if abs(pct) < 0.5:
        return "no change " + vs
    return f"{'+' if pct > 0 else '−'}{abs(pct):.0f}% {vs}"


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


def share(x, total):
    return x / total if x is not None and total else None


def dns_comparable(before):
    """The previous period can be compared when its server answered and held the whole period."""
    return bool(
        before
        and not before["error"]
        and before["total"]
        and (before["coverage"] is None or before["coverage"] >= 0.95)
    )


def dns_pairs(d, p):
    """[(this period, previous period)] for each DNS server in the report."""
    prev = p.get("dns") or []
    return [(c, prev[i] if i < len(prev) else None) for i, c in enumerate(d.get("dns") or [])]


def points_change(cur, before, word):
    """'+3 points vs last week' for shares, or '' when there is nothing to compare."""
    if cur is None or before is None:
        return ""
    diff = (cur - before) * 100
    return f"same as last {word}" if abs(diff) < 0.5 else f"{diff:+.0f} points vs last {word}"


def delta(cur, prev, covered=True):
    """A change for a table cell whose heading already says what it compares with: '+12%', '+3', 'same'
    or '–' when there's nothing to compare."""
    if not covered or cur is None or prev is None:
        return "–"
    if prev == 0:
        return "new" if cur else "same"
    diff = cur - prev
    if abs(prev) < 20:  # small counts read better as numbers
        return "same" if round(diff) == 0 else f"{'+' if diff > 0 else '−'}{abs(diff):.0f}"
    pct = diff / prev * 100
    return "same" if abs(pct) < 0.5 else f"{'+' if pct > 0 else '−'}{abs(pct):.0f}%"


def summary(d, p, word, covered, on=None):
    """Plain-language lines for page 1, written from the numbers.
    Only covers the sections that are switched on."""
    on = on if on is not None else {k for k, *_ in SECTIONS}
    lines = []
    up = [w for w in d["wans"] if w["uptime"] is not None]
    if up and "internet" in on:
        prim = max(up, key=lambda w: w["uptime"])
        fo = d["failovers"] or 0
        tail = (
            f" It failed over to a backup line {fo:.0f} time{'s' * (fo != 1)}"
            if fo
            else " There were no failovers to the backup lines"
        )
        lines.append(f"The main internet line ({prim['name']}) was up {prim['uptime']:.2f}% of the {word}.{tail}.")
    isp = d.get("isp")
    if isp and "isp" in on:
        probs = []
        for l in isp["lines"]:
            bits = []
            if l["outages"]:
                bits.append(
                    f"went down {_times(l['outages'])}"
                    + (f" ({fduration(l['down_s'])} in total)" if l["down_s"] else "")
                )
            if l["latency"]:
                bits.append(f"had high latency {_times(l['latency'])}")
            if l["loss"]:
                bits.append(f"lost packets {_times(l['loss'])}")
            if bits:
                probs.append(f"{l['name']} " + _join(bits))
        lines.append(
            ("Internet lines: " + "; ".join(probs) + ".")
            if probs
            else f"No internet line reported outages, high latency or packet loss this {word}."
        )
    if d["wan_down"] is not None and "downloaded" in on:
        ch = change(d["wan_down"], p.get("wan_down"), "", word, covered["prom"])
        lines.append(
            f"The network downloaded {fbytes(d['wan_down'])} and uploaded {fbytes(d['wan_up'])}"
            + (f" ({ch})" if ch and not ch.startswith("no earlier") else "")
            + "."
        )
    if d["clients_peak"] is not None and on & {"devices", "aps"}:
        new = d["clients_new"] if covered["prom"] else None
        lines.append(
            f"{fnum(d['clients_peak'])} devices were online at the busiest moment, "
            f"{fnum(d['clients_unique'])} different devices over the {word}"
            + (f", {fnum(new)} of them seen for the first time" if new else "")
            + "."
        )
    if d["blocks_in"] is not None and on & {"security", "threats"}:
        lines.append(
            f"The firewall blocked {fnum(d['blocks_in'])} connection attempts from the internet "
            f"and {fnum(d['blocks_out'])} from devices inside the network. "
            f"Intrusion prevention blocked {fnum(d['threats_blocked'])} of {fnum(d['threats'])} threats"
            + (
                (
                    ", all of them high severity"
                    if d["threats_high"] == d["threats"]
                    else f", {fnum(d['threats_high'])} of them high severity"
                )
                if d["threats_high"]
                else ""
            )
            + "."
        )
    if "dns" in on:
        for c, before in dns_pairs(d, p):
            if c["error"] or not c["total"]:
                continue
            blocked = share(c["blocked"], c["total"])
            ch = (
                points_change(blocked, share(before["blocked"], before["total"]), word)
                if dns_comparable(before)
                else ""
            )
            lines.append(
                f"{c['name']} answered {fnum(c['total'])} DNS queries"
                + (f" from {fnum(c['clients'])} devices" if c["clients"] else "")
                + f" and blocked {fpct(blocked, 1 if blocked < 0.1 else 0)} of them"
                + (f" ({ch})" if ch else "")
                + "."
            )
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
        a.append(
            f"{fnum(d['threats_high'])} high-severity threats were detected"
            + (", all blocked" if not d["threats_open"] else "")
            + (f"; {top['n']:.0f} of them involved {_who(top['name'])}" if top else "")
        )
    if "security" in on and d["logins_outside"]:
        own = d.get("own_ips", set())
        ips = ", ".join(
            f"{r['name']} ({r['n']:.0f}" + (", your own internet address" if r["name"] in own else "") + ")"
            for r in d.get("login_ips", [])
        )
        a.append(
            f"{fnum(d['logins_outside'])} admin logins came from outside the local network"
            + (f", from {ips}; check these are your admins" if ips else "")
        )
    if aps:
        busy = sorted((ap for ap in d["aps"] if (ap["u24"] or 0) > 0.6), key=lambda ap: -ap["u24"])
        if busy:
            a.append(
                "2.4 GHz channel busy most of the time on "
                + _join(f"{ap['name']} ({fpct(ap['u24'])})" for ap in busy)
                + "; devices there will feel slow"
            )
        resend = sorted((ap for ap in d["aps"] if (ap.get("retries") or 0) > 0.15), key=lambda ap: -ap["retries"])
        if resend:
            a.append(
                "Access points resending many Wi-Fi packets (healthy is under 15%): "
                + _join(f"{ap['name']} {fpct(ap['retries'])}" for ap in resend)
                + "; a sign of interference or devices with weak signal"
            )
        weak_ap = sorted(
            (ap for ap in d["aps"] if ap.get("signal") is not None and ap["signal"] < WEAK_DBM),
            key=lambda ap: ap["signal"],
        )
        if weak_ap:
            a.append(
                "Devices averaged a weak signal on "
                + _join(f"{ap['name']} ({ap['signal']:.0f} dBm)" for ap in weak_ap)
                + "; they're too far from the access point or behind walls"
            )
    a_ = d.get("dpi")
    if a_ and "apps" in on and a_["p2p"] > 0.05:
        who = a_.get("p2p_devices") or []
        src = ""
        if who:
            first = who[0]
            src = (
                f", nearly all from {first['name']}"
                if first["share"] > 0.9
                else "; mostly from " + _join(f"{w['name']} ({fpct(w['share'])})" for w in who)
            )
        a.append(
            f"Peer-to-peer file sharing (such as BitTorrent) was {fpct(a_['p2p'])} of identified traffic{src}; "
            "check it's allowed, it often carries pirated content and malware"
        )
    w = d.get("wifi")
    if w and "wifi" in on:
        if w["weak"]:
            n = len(w["weak"])
            names = _join(f"{x['name']}" + (f" on {x['ap']}" if x["ap"] else "") for x in w["weak"][:3])
            a.append(
                f"{n} device{'s' * (n != 1)} had a weak Wi-Fi signal on average (below {WEAK_DBM} dBm): {names}"
                + (f" and {n - 3} more" if n > 3 else "")
            )
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
                a.append(
                    f"Internet line {l['name']} went down {_times(l['outages'])}"
                    + (f", {fduration(l['down_s'])} in total" if l["down_s"] else "")
                )
            if l["latency"] + l["loss"] >= 3:
                a.append(
                    f"{l['name']} reported high latency {l['latency']} and packet loss {l['loss']} times; "
                    "worth raising with the provider"
                )
            if l["promised"] and l["dl"] and l["dl"] < 0.5 * l["promised"]:
                a.append(
                    f"{l['name']} speed tests averaged {l['dl']:.0f} Mbps, under half the {l['promised']:.0f} Mbps "
                    "the plan promises"
                )
            if l["state"] and l["state"] not in ("Active", "Backup"):
                a.append(f"Internet line {l['name']} is {l['state'].lower()}")
    m = d.get("maint")
    if m and "maintenance" in on:
        if m["updates"]:
            a.append(
                f"Firmware updates are waiting for {_join(u['name'] for u in m['updates'][:4])}"
                + (f" and {len(m['updates']) - 4} more" if len(m["updates"]) > 4 else "")
            )
        for p_ in m["pools"]:
            if p_["peak"] >= 0.8:
                a.append(
                    f"The {p_['network']} address pool reached {fpct(p_['peak'])} full; new devices can't "
                    "connect once it's full, so enlarge it or shorten the lease time"
                )
        for st in m["storage"]:
            if (st["pct"] or 0) >= 0.85:
                a.append(f"Console storage '{st['name']}' is {fpct(st['pct'])} full")
        for f in m["sfp"]:
            if f["rx"] is not None and f["rx"] < SFP_WEAK_DBM:
                a.append(f"The fibre module on {f['port']} receives a weak {f['rx']:.1f} dBm; clean or check the fibre")
            elif f["drop"] is not None and f["drop"] >= SFP_DROP_DB:
                a.append(
                    f"The fibre signal on {f['port']} fell {f['drop']:.1f} dB over the {d.get('_word', 'period')}; "
                    "a connector or the fibre may be degrading"
                )
            if (f["temp"] or 0) >= 70:
                a.append(f"The fibre module on {f['port']} reached {f['temp']:.0f} °C")
    if "dns" in on:
        word = d.get("_word", "period")
        for c, before in dns_pairs(d, {"dns": d.get("_dns_prev")}):
            if c["error"]:
                a.append(f"{c['name']} couldn't be read: {c['error']}. Its DNS figures are missing")
                continue
            blocked = share(c["blocked"], c["total"])
            was = share(before["blocked"], before["total"]) if dns_comparable(before) else None
            if blocked is not None and was is not None and blocked - was > 0.10:
                top = c["top_clients"][0]["name"] if c["top_clients"] else None
                a.append(
                    f"{c['name']} blocked {fpct(blocked)} of queries, up from {fpct(was)} last {word}"
                    + (f"; {top} asked the most" if top else "")
                    + ". Check the top blocked domains for a device that keeps calling a blocked service"
                )
            failed = share(c["failed"], c["total"])
            if failed is not None and failed > 0.02:
                a.append(
                    f"{fpct(failed, 1)} of lookups on {c['name']} failed (server failure); check its upstream "
                    "or forwarder servers"
                )
            if c["blocklist_updated"]:
                age = (dt.datetime.now(dt.timezone.utc) - c["blocklist_updated"]).days
                if age > 14:
                    a.append(f"{c['name']} blocklists were last updated {age} days ago; run a gravity update")
    return a
