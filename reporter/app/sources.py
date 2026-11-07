"""Prometheus and Loki access, and the log queries the report uses."""

import json
import math
import urllib.parse
import urllib.request

from .config import LOKI, PROM, log

# private address ranges, same as the firewall dashboard
PRIVATE = r"(10|127)\..*|192\.168\..*|172\.(1[6-9]|2[0-9]|3[01])\..*|f[cd][0-9a-f]{2}:.*|fe80:.*"


FW = (
    '{application="unifi_system_log", category="SECURITY"} |= `"subcategory":"SECURITY_FIREWALL"` '
    '| json sub="subcategory", policy="parameters.TRIGGER.name", sip="parameters.SRC_IP.name", '
    'scl="parameters.SRC_CLIENT.name", sclip="parameters.SRC_CLIENT.ip", dip="parameters.DST_IP.name", '
    'ddev="parameters.DST_DEVICE.name" | sub="SECURITY_FIREWALL" '
    "| label_format src=`{{ or .scl .sip }}`, srcaddr=`{{ or .sclip .sip }}`"
)


FW_IN = FW + f" | srcaddr !~ `{PRIVATE}`"


FW_OUT = FW + f" | srcaddr =~ `{PRIVATE}`"


IPS = (
    '{application="unifi_system_log", category="SECURITY"} != `"subcategory":"SECURITY_FIREWALL"` '
    '| json sub="subcategory", evt="event", scl="parameters.SRC_CLIENT.name", sip="parameters.SRC_IP.name", sev="severity" '
    '| sub!="SECURITY_FIREWALL" | label_format src=`{{ or .scl .sip }}`, '
    'type=`{{ regexReplaceAll "_" (regexReplaceAll "^SECURITY_" .sub "") " " }}`'
)


AUDIT_FW = (
    '{application="unifi_system_log", category="AUDIT"} |= `FIREWALL` '
    '| json evt="event", section="parameters.SECTION.id" | section=~`FIREWALL_.*|NAT`'
)


LOGINS = (
    '{application="unifi_system_log", category="AUDIT"} |= `"event":"ADMIN_ACCESS"` '
    '| json evt="event", ip="parameters.IP.name" | evt="ADMIN_ACCESS"'
)


WAN_EVENTS = '{application="unifi_system_log", category="INTERNET_AND_WAN"}'


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
        return self._safe(
            lambda q: _get(PROM + "/api/v1/query", {"query": q.replace("$R", self.r), "time": self.end.timestamp()}), q
        )

    def prom_range(self, q, step):
        return self._safe(
            lambda q: _get(
                PROM + "/api/v1/query_range",
                {"query": q, "start": self.start.timestamp() + step, "end": self.end.timestamp(), "step": step},
            ),
            q,
        )

    def loki(self, q):
        return self._safe(
            lambda q: _get(
                LOKI + "/loki/api/v1/query", {"query": q.replace("$R", self.r), "time": int(self.end.timestamp() * 1e9)}
            ),
            q,
        )

    def loki_lines(self, q, limit=500):
        return self._safe(
            lambda q: _get(
                LOKI + "/loki/api/v1/query_range",
                {
                    "query": q,
                    "start": int(self.start.timestamp() * 1e9),
                    "end": int(self.end.timestamp() * 1e9),
                    "limit": limit,
                    "direction": "forward",
                },
            ),
            q,
        )

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
        l = self._safe(
            lambda q: _get(
                LOKI + "/loki/api/v1/query_range",
                {
                    "query": q,
                    "start": int((self.start.timestamp() + 86400) * 1e9),
                    "end": int(self.end.timestamp() * 1e9),
                    "step": "86400",
                },
            ),
            'sum(count_over_time({application="unifi_system_log"}[1d]))',
        )
        return {"prom": frac(p, 1440 * 0.98), "loki": frac(l)}


CLIENT_LOG = '{application="unifi_system_log", category="CLIENT_DEVICES"}'


DEVICE_LOG = '{application="unifi_system_log", category="UNIFI_DEVICES"}'


DPI_RX = 'unpoller_client_dpi_receive_bytes{mac!="TOTAL"}'


DPI_TX = 'unpoller_client_dpi_transmit_bytes{mac!="TOTAL"}'
