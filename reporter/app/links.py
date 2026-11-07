"""Links from the PDF into the Grafana dashboards."""

import urllib.parse
import urllib.request

# dashboard uids in grafana/provisioning/dashboards (kept stable on purpose)
DASHBOARDS = {
    "sites": "9WaGWZaZk",
    "gateway": "4Yo8IZ-Wk",
    "aps": "g5wFWqxZk",
    "clients": "jMfvAjxWz",
    "dpi": "w3usaHLZk",
    "firewall": "unpoller-firewall",
}


# report section heading -> dashboard that shows the same thing in detail
SECTION_DASHBOARD = {
    "Internet throughput": "gateway",
    "Trends": "sites",
    "Internet and gateway": "gateway",
    "Internet lines and quality": "gateway",
    "Devices": "clients",
    "Access points": "aps",
    "Applications": "dpi",
    "Wi-Fi experience and stability": "clients",
    "Security": "firewall",
    "Threats blocked": "firewall",
    "Maintenance": "gateway",
    "Changes and events": "firewall",
}


def grafana_linker(settings, start, end):
    """Returns link(dashboard, **variables) -> URL set to the report's dates, or None when links are off."""
    g = settings["general"]
    base = (g.get("grafana_url") or "").strip().rstrip("/")
    if not base or not g.get("grafana_links", True):
        return None
    span = f"from={int(start.timestamp() * 1000)}&to={int(end.timestamp() * 1000)}"

    def link(dash, **variables):
        qs = "".join(f"&var-{k}={urllib.parse.quote(str(v))}" for k, v in variables.items() if v)
        return f"{base}/d/{DASHBOARDS[dash]}?orgId=1&{span}{qs}"

    return link
