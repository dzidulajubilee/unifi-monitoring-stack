"""Optional DNS section: query figures read from Pi-hole (v6) and Technitium DNS Server.

Both keep their own history, so the report asks them for its dates when it is built; nothing new
runs between reports. Each server becomes one dict of plain figures (see `_blank`), the same shape
for both kinds, so the template, summary and attention lines don't care which kind it is.
A server that can't be reached gives a dict with `error` set instead of breaking the report.
"""

import datetime as dt
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

from .config import log

KINDS = {"pihole": "Pi-hole", "technitium": "Technitium"}
TOP = 8


def servers(settings):
    """Configured servers, in the order they were added; blank rows are ignored."""
    return [x for x in settings.get("dns", {}).get("servers", []) if x.get("url")]


def _blank(srv):
    return {
        "name": srv.get("name") or KINDS.get(srv.get("kind"), "DNS server"),
        "kind": srv.get("kind"),
        "url": srv.get("url"),
        "version": None,
        "error": None,
        "total": None,
        "blocked": None,
        "cached": None,
        "forwarded": None,  # Pi-hole: sent to the upstream servers
        "recursive": None,  # Technitium: resolved by the server itself
        "failed": None,  # Technitium: SERVFAIL answers
        "nxdomain": None,  # Technitium: names that don't exist
        "clients": None,
        "blocklist_domains": None,
        "blocklist_updated": None,  # Pi-hole: when the blocklists were last refreshed
        "series": [],  # [(unix time, total, blocked)]
        "top_domains": [],
        "top_blocked": [],
        "top_clients": [],
        "upstreams": [],
        "coverage": None,  # Pi-hole: share of the period its database still holds
    }


class _Http:
    def __init__(self, base, verify=True, headers=None, timeout=120):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.headers = headers or {}
        self.ctx = None
        if not verify:  # also covers http addresses that redirect to https, as Technitium does
            self.ctx = ssl.create_default_context()
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE

    def call(self, path, params=None, method="GET", body=None):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=dict(self.headers))
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as r:
            raw = r.read()
        return json.loads(raw) if raw else {}


def _err(e):
    """A short reason a reader can act on."""
    if isinstance(e, urllib.error.HTTPError):
        if e.code in (401, 403):
            return "the password or token was refused"
        return f"it answered HTTP {e.code}"
    if isinstance(e, urllib.error.URLError):
        r = e.reason
        if isinstance(r, ssl.SSLCertVerificationError):
            return "its HTTPS certificate isn't trusted (tick Don't check the certificate if it's self-signed)"
        return f"it couldn't be reached ({r})"
    if isinstance(e, TimeoutError):
        return "it didn't answer in time"
    return str(e) or e.__class__.__name__


def _rows(items, name, n="count"):
    return [{"name": x.get(name) or "–", "n": float(x.get(n) or 0)} for x in items[:TOP]]


# ------------------------------------------------------------------ Pi-hole v6


class PiHole:
    def __init__(self, srv):
        # database queries take about 10 s per month of history each, so a quarter needs room
        self.http = _Http(srv["url"], srv.get("verify", True), timeout=300)
        self.password = srv.get("secret", "")
        self.sid = None

    def __enter__(self):
        try:
            r = self.http.call("/api/auth", method="POST", body={"password": self.password})
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise RuntimeError("the password was refused") from e
            if e.code == 429:  # either too many failed logins or every API session in use
                try:
                    key = json.loads(e.read() or b"{}").get("error", {}).get("key")
                except ValueError:
                    key = None
                if key == "api_seats_exceeded":
                    raise RuntimeError("Pi-hole has no free API sessions; raise webserver.api.max_sessions") from e
                raise RuntimeError(
                    "Pi-hole is refusing logins for a while after failed attempts; try again later"
                ) from e
            raise
        s = r.get("session", {})
        if not s.get("valid"):
            raise RuntimeError(s.get("message") or "the password was refused")
        self.sid = s.get("sid")  # None when Pi-hole has no password
        if self.sid:
            self.http.headers["X-FTL-SID"] = self.sid
        return self

    def __exit__(self, *exc):
        if self.sid:  # sessions are limited, so give this one back
            try:
                self.http.call("/api/auth", method="DELETE")
            except Exception:  # noqa: BLE001
                pass

    def get(self, path, **params):
        return self.http.call("/api" + path, params)

    def period(self, start, end, out, full=True):
        rng = {"from": int(start.timestamp()), "until": int(end.timestamp())}
        hist = self.get("/history/database", **rng).get("history", [])
        if full:  # the summary only adds the device count; the comparison period does without it
            s = self.get("/stats/database/summary", **rng)
            out["total"] = s.get("sum_queries")
            out["blocked"] = s.get("sum_blocked")
            out["clients"] = s.get("total_clients")
        else:
            out["total"] = sum(h.get("total", 0) for h in hist)
            out["blocked"] = sum(h.get("blocked", 0) for h in hist)
        if hist:
            out["cached"] = sum(h.get("cached", 0) for h in hist)
            out["forwarded"] = sum(h.get("forwarded", 0) for h in hist)
            first = min(h["timestamp"] for h in hist)
            span = (end - start).total_seconds()
            out["coverage"] = max(0.0, min(1.0, (end.timestamp() - first) / span)) if span else 1.0
        else:
            out["coverage"] = 0.0
        if not full:
            return out
        out["series"] = [(h["timestamp"], h.get("total", 0), h.get("blocked", 0)) for h in hist]
        out["top_domains"] = _rows(
            self.get("/stats/database/top_domains", **rng, count=TOP).get("domains", []), "domain"
        )
        out["top_blocked"] = _rows(
            self.get("/stats/database/top_domains", **rng, count=TOP, blocked="true").get("domains", []), "domain"
        )
        out["top_clients"] = [
            {"name": c.get("name") or c.get("ip"), "ip": c.get("ip"), "n": float(c.get("count") or 0)}
            for c in self.get("/stats/database/top_clients", **rng, count=TOP).get("clients", [])[:TOP]
        ]
        ups = self.get("/stats/database/upstreams", **rng).get("upstreams", [])
        out["upstreams"] = [
            {"name": u.get("name") or u.get("ip") or "–", "n": float(u.get("count") or 0)}
            for u in sorted(ups, key=lambda u: -(u.get("count") or 0))
            if (u.get("port") or -1) != -1  # -1 is Pi-hole's own cache and blocklist, not an upstream
        ][:TOP]
        g = self.get("/stats/summary").get("gravity", {})
        out["blocklist_domains"] = g.get("domains_being_blocked")
        if g.get("last_update"):
            out["blocklist_updated"] = dt.datetime.fromtimestamp(g["last_update"], dt.timezone.utc)
        try:
            v = self.get("/info/version").get("version", {})
            out["version"] = "Pi-hole " + (v.get("core", {}).get("local", {}).get("version") or "").lstrip("v")
        except Exception:  # noqa: BLE001 - the version is a nicety
            pass
        return out


# ------------------------------------------------------------------ Technitium


def _iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse_label(s):
    """Technitium's chart labels: '2024-02-04T10:38:00.0000000Z' (7 decimals) or a plain date."""
    s = re.sub(r"(\.\d{6})\d+", r"\1", s.strip()).replace("Z", "+00:00")
    t = dt.datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


class Technitium:
    def __init__(self, srv):
        self.http = _Http(srv["url"], srv.get("verify", True), {"Authorization": f"Bearer {srv.get('secret', '')}"})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def get(self, path, **params):
        r = self.http.call("/api" + path, params)
        if r.get("status") == "invalid-token":
            raise RuntimeError("the API token was refused")
        if r.get("status") != "ok":
            raise RuntimeError(r.get("errorMessage") or f"status {r.get('status')}")
        return r.get("response", r)

    # Technitium answers a custom range in hourly buckets up to about 6½ days and in UTC-day buckets
    # beyond that, and it counts the bucket holding the end time as part of the range. Asking in chunks
    # of at most 6 days, each ending an hour early, gets exactly the report's hours in any time zone.
    CHUNK = dt.timedelta(days=6)
    SUMS = {
        "total": "totalQueries",
        "blocked": "totalBlocked",
        "cached": "totalCached",
        "recursive": "totalRecursive",
        "failed": "totalServerFailure",
        "nxdomain": "totalNxDomain",
    }

    def period(self, start, end, out, full=True):
        for k in self.SUMS:
            out[k] = 0
        tops = {"TopDomains": {}, "TopBlockedDomains": {}, "TopClients": {}}
        t = start
        while t < end:
            e = min(t + self.CHUNK, end)
            rng = {"start": _iso(t), "end": _iso(e - dt.timedelta(hours=1))}
            r = self.get("/dashboard/stats/get", type="Custom", utc="true", **rng)
            st = r.get("stats", {})
            for k, key in self.SUMS.items():
                out[k] += st.get(key) or 0
            out["blocklist_domains"] = st.get("blockListZones")
            if full:
                ch = r.get("mainChartData", {})
                sets = {x.get("label"): x.get("data", []) for x in ch.get("datasets", [])}
                total, blocked = sets.get("Total", []), sets.get("Blocked", [])
                for i, label in enumerate(ch.get("labels", [])):
                    try:
                        hour = _parse_label(label).timestamp() - 3600  # a label marks the end of its hour
                    except ValueError:
                        continue
                    out["series"].append(
                        (hour, total[i] if i < len(total) else 0, blocked[i] if i < len(blocked) else 0)
                    )
                for kind, seen in tops.items():
                    extra = {"noReverseLookup": "true"} if kind == "TopClients" else {}
                    res = self.get("/dashboard/stats/getTop", type="custom", statsType=kind, limit=1000, **rng, **extra)
                    for x in next(iter(res.values()), []) if isinstance(res, dict) else []:
                        seen[x.get("name")] = seen.get(x.get("name"), 0) + (x.get("hits") or 0)
            t = e
        # With NXDOMAIN blocking (the default), blocked answers are counted as names that don't exist too
        try:
            mode = self.get("/settings/get").get("blockingType")
        except Exception:  # noqa: BLE001 - needs Settings: View; without it the row is left out
            mode = None
        if mode == "NxDomain":
            out["nxdomain"] = max(0, out["nxdomain"] - out["blocked"])
        elif mode is None:
            out["nxdomain"] = None
        if not full:
            return out
        best = lambda d: sorted(d.items(), key=lambda kv: -kv[1])[:TOP]  # noqa: E731
        out["top_domains"] = [{"name": k, "n": float(v)} for k, v in best(tops["TopDomains"])]
        out["top_blocked"] = [{"name": k, "n": float(v)} for k, v in best(tops["TopBlockedDomains"])]
        out["top_clients"] = [{"name": k, "ip": k, "n": float(v)} for k, v in best(tops["TopClients"])]
        out["clients"] = len(tops["TopClients"])  # different addresses over the whole period
        try:
            info = self.http.call("/api/user/session/get").get("info", {})
            if info.get("version"):
                out["version"] = f"Technitium {info['version']}"
        except Exception:  # noqa: BLE001
            pass
        return out


CLIENTS = {"pihole": PiHole, "technitium": Technitium}


def read(srv, start, end, prev_start):
    """(this period, previous period) for one server; on failure both carry `error`."""
    cur, prev = _blank(srv), _blank(srv)
    try:
        with CLIENTS[srv["kind"]](srv) as c:
            c.period(start, end, cur)
            try:
                c.period(prev_start, start, prev, full=False)
            except Exception as e:  # noqa: BLE001 - only the comparison is lost
                prev = _blank(srv)
                prev["error"] = _err(e)
    except Exception as e:  # noqa: BLE001 - one server must not stop the report
        log.warning("DNS server %s (%s) failed: %s", cur["name"], srv.get("url"), e)
        cur, prev = _blank(srv), _blank(srv)  # no half-read figures: blanks show as –
        cur["error"] = prev["error"] = _err(e)
    return cur, prev


def test(srv):
    """For the Settings page: None when the server answers, otherwise the reason."""
    now = dt.datetime.now(dt.timezone.utc)
    try:
        with CLIENTS[srv["kind"]](srv) as c:
            c.period(now - dt.timedelta(hours=1), now, _blank(srv), full=False)
        return None
    except Exception as e:  # noqa: BLE001
        return _err(e)


def name_clients(rows, names):
    """Swap IP addresses for the device names UniFi knows. `names`: ip -> label."""
    for r in rows:
        ip = r.get("ip") or ""
        if ip in ("127.0.0.1", "::1"):
            r["name"], r["detail"] = "The DNS server itself", ip
            continue
        if ip in names:  # UniFi's name beats the DNS server's own guess (often a reverse-lookup name)
            r["name"], r["detail"] = names[ip], ip
        elif ip and r["name"] != ip:
            r["detail"] = ip
    return rows


def buckets(series, start, end):
    """Totals per hour (periods up to two days) or per day, for the chart: [(bucket start, total, blocked)]."""
    step = 3600 if (end - start).total_seconds() <= 2 * 86400 else 86400
    t0 = start.timestamp()
    n = max(1, round((end.timestamp() - t0) / step))
    tot, blk = [0.0] * n, [0.0] * n
    seen = [False] * n
    for t, a, b in series:
        i = int((t - t0) // step)
        if 0 <= i < n:
            tot[i] += a or 0
            blk[i] += b or 0
            seen[i] = True
    return [(t0 + i * step, tot[i] if seen[i] else None, blk[i] if seen[i] else None) for i in range(n)], step
