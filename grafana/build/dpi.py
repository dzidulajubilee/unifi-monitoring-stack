"""Generates grafana/provisioning/dashboards/unifi-dpi.json (UniFi Applications).

Self-contained (predates gf.py). UniFi's DPI figures are rolling last-24-hours totals per device and
application, not counters: never use increase()/rate() on them.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "provisioning" / "dashboards" / "unifi-dpi.json"
DS = {"type": "prometheus", "uid": "Prometheus"}

F = 'source=~"$Controller", site_name=~"$Site", mac!="TOTAL", name=~"$Device"'
RX = f"unpoller_client_dpi_receive_bytes{{{F}}}"
TX = f"unpoller_client_dpi_transmit_bytes{{{F}}}"


def rx_cat(extra):
    return f'unpoller_client_dpi_receive_bytes{{{F}, {extra}}}'


def tx_cat(extra):
    return f'unpoller_client_dpi_transmit_bytes{{{F}, {extra}}}'


def clean_cat(x):
    # UniFi suffixes some categories with an id: "Unknown_255", "Network Protocols_20"
    return f'label_replace({x}, "category", "$1", "category", "(.+)_[0-9]+")'


def clean_app(x):
    return f'label_replace({x}, "application", "$1 $2", "application", "(.+)_(.+)")'


def share(match):
    """Share of all identified traffic whose series match (0 when nothing matches)."""
    return f"((sum({rx_cat(match)}) + sum({tx_cat(match)})) or vector(0)) / {TOTAL}"


BOTH_CAT = f"sum by (category) ({clean_cat(RX)}) + sum by (category) ({clean_cat(TX)})"
TOTAL = f"(sum({RX}) + sum({TX}))"

# One colour per category, the same in every panel (validated categorical palette, dark steps).
# Everything else stays neutral grey so the eye goes to the categories that carry the traffic.
CATEGORY_COLORS = {
    "Network Protocols": "#3987e5",
    "Media Streaming": "#d95926",
    "Social Networks": "#199e70",
    "Web Services": "#c98500",
    "Instant Messengers": "#d55181",
    "Security Updates": "#008300",
    "Peer-to-Peer Networks": "#9085e9",
    "Unknown": "#8a8984",
}
OTHER = "#5c5b57"
ACCENT = "#3987e5"


def color_overrides():
    return [{"matcher": {"id": "byName", "options": name},
             "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": col}}]}
            for name, col in CATEGORY_COLORS.items()]


def target(expr, legend="", ref="A", instant=True, fmt="time_series", interval=None):
    t = {"datasource": DS, "editorMode": "code", "expr": expr, "legendFormat": legend, "refId": ref,
         "range": not instant, "instant": instant, "format": fmt}
    if interval:
        t["interval"] = interval
    return t


panels = []
pid = 0


def add(p):
    global pid
    pid += 1
    p["id"] = pid
    p.setdefault("datasource", DS)
    panels.append(p)


def stat(title, desc, expr, x, w, unit="decbytes", decimals=1, thresholds=None):
    add({
        "type": "stat", "title": title, "description": desc,
        "gridPos": {"h": 4, "w": w, "x": x, "y": 0},
        "targets": [target(expr)],
        "options": {"colorMode": "value", "graphMode": "none", "justifyMode": "auto", "orientation": "auto",
                    "textMode": "value", "wideLayout": True, "showPercentChange": False,
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
        "fieldConfig": {"defaults": {
            "unit": unit, "decimals": decimals, "noValue": "–", "mappings": [],
            "color": {"mode": "thresholds"},
            "thresholds": thresholds or {"mode": "absolute", "steps": [{"color": "text", "value": None}]}},
            "overrides": []},
    })


# ---- row 1: the numbers
stat("Traffic identified, last 24 h",
     "All traffic UniFi's traffic identification (DPI) recognised in the 24 hours up to the end of the selected time "
     "range, downloads and uploads together. Includes traffic between devices on the network, so it can be a little "
     "more than the internet line carried.",
     TOTAL, 0, 5)
stat("Downloaded, last 24 h", "Traffic received by devices, as identified by DPI.", f"sum({RX})", 5, 4)
stat("Uploaded, last 24 h", "Traffic sent by devices, as identified by DPI.", f"sum({TX})", 9, 4)
stat("Devices", "Devices with application traffic in the last 24 hours.",
     f"count(count by (mac) ({RX}))", 13, 3, unit="none", decimals=0)
stat("Unrecognised",
     "Share of traffic DPI couldn't name (categories called Unknown). Encrypted traffic is usually still recognised "
     "by its service; a high share here often means VPNs or unusual apps.",
     share('category=~"Unknown.*"'),
     16, 4, unit="percentunit", decimals=1)
stat("Peer-to-peer",
     "Share of traffic that was peer-to-peer file sharing, such as BitTorrent. Turns orange above 5%: it is often "
     "against office policy and can carry pirated content or malware. See which device in the table below.",
     share('category=~"Peer-to-Peer.*"'),
     20, 4, unit="percentunit", decimals=1,
     thresholds={"mode": "absolute", "steps": [{"color": "text", "value": None}, {"color": "orange", "value": 0.05}]})


def bars(title, desc, expr, legend, x, w, h, y, fixed=None, by_category=False):
    fc = {"defaults": {"unit": "decbytes", "decimals": 1, "min": 0, "mappings": [], "noValue": "No data",
                       "color": {"mode": "fixed", "fixedColor": fixed or OTHER},
                       "thresholds": {"mode": "absolute", "steps": [{"color": "text", "value": None}]}},
          "overrides": color_overrides() if by_category else []}
    add({
        "type": "bargauge", "title": title, "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [target(expr, legend)],
        "options": {"displayMode": "basic", "orientation": "horizontal", "namePlacement": "left",
                    "valueMode": "text", "showUnfilled": True, "sizing": "manual", "minVizHeight": 16,
                    "maxVizHeight": 22, "minVizWidth": 8,
                    "legend": {"showLegend": False, "displayMode": "list", "placement": "bottom", "calcs": []},
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
        "fieldConfig": fc,
    })


# ---- row 2: what the traffic is (the main moment: one accent colour for applications)
bars("Top applications, last 24 h",
     "The 15 applications with the most traffic in the last 24 hours, downloads and uploads together. "
     "SSL/TLS and Unknown are encrypted or unrecognised traffic DPI couldn't attribute to a named app.",
     f"sort_desc(topk(15, sum by (application) ({clean_app(RX)}) + sum by (application) ({clean_app(TX)})))",
     "{{application}}", 0, 13, 13, 4, fixed=ACCENT)
bars("By category, last 24 h",
     "The 15 categories with the most traffic, grouped by UniFi's application categories. Each category keeps "
     "the same colour in every panel.",
     f"sort_desc(topk(15, {BOTH_CAT}))", "{{category}}", 13, 11, 13, 4, by_category=True)

# ---- row 3: per day
add({
    "type": "timeseries", "title": "Traffic per day by category",
    "description": "Each bar is one day's traffic, taken from UniFi's rolling 24-hour figure at midnight (UTC). "
                   "Today's bar appears tomorrow, once the day is complete. Shows the last 14 days regardless of "
                   "the time picker.",
    "gridPos": {"h": 10, "w": 24, "x": 0, "y": 17},
    "timeFrom": "14d", "hideTimeOverride": False,
    "targets": [target(f"{BOTH_CAT.replace(RX, RX + ' offset -1d').replace(TX, TX + ' offset -1d')}",
                       "{{category}}", instant=False, interval="1d")],
    "options": {"legend": {"displayMode": "table", "placement": "right", "showLegend": True,
                           "calcs": ["sum"], "sortBy": "Total", "sortDesc": True, "width": 300},
                "tooltip": {"mode": "multi", "sort": "desc"}},
    "fieldConfig": {"defaults": {
        "unit": "decbytes", "decimals": 1, "min": 0, "mappings": [],
        "color": {"mode": "fixed", "fixedColor": OTHER},
        "custom": {"drawStyle": "bars", "barAlignment": 0, "barWidthFactor": 0.7, "fillOpacity": 85,
                   "lineWidth": 0, "gradientMode": "none", "showPoints": "never",
                   "stacking": {"mode": "normal", "group": "A"}, "axisSoftMin": 0, "axisPlacement": "auto",
                   "thresholdsStyle": {"mode": "off"}},
        "thresholds": {"mode": "absolute", "steps": [{"color": "text", "value": None}]}},
        "overrides": color_overrides()},
})

# ---- row 4: who
add({"type": "row", "title": "Devices", "collapsed": False, "gridPos": {"h": 1, "w": 24, "x": 0, "y": 27}, "panels": []})


def table(title, desc, queries, y, h, organize, sort_by, filterable=True):
    add({
        "type": "table", "title": title, "description": desc,
        "gridPos": {"h": h, "w": 24, "x": 0, "y": y},
        "targets": queries,
        "transformations": [
            {"id": "merge", "options": {}},
            {"id": "calculateField", "options": {"mode": "binary", "alias": "Total",
                                                 "binary": {"left": "Value #A", "operator": "+", "right": "Value #B"},
                                                 "replaceFields": False}},
            {"id": "organize", "options": organize},
            {"id": "sortBy", "options": {"fields": {}, "sort": [{"field": sort_by, "desc": True}]}},
        ],
        "options": {"cellHeight": "sm", "showHeader": True,
                    "footer": {"show": False, "reducer": ["sum"], "fields": "", "countRows": False}},
        "fieldConfig": {"defaults": {"mappings": [], "color": {"mode": "fixed", "fixedColor": ACCENT},
                                     "custom": {"align": "auto", "filterable": filterable, "inspect": False,
                                                "cellOptions": {"type": "auto"}},
                                     "thresholds": {"mode": "absolute", "steps": [{"color": "text", "value": None}]}},
                        "overrides": [
                            {"matcher": {"id": "byRegexp", "options": "Downloaded|Uploaded"},
                             "properties": [{"id": "unit", "value": "decbytes"}, {"id": "decimals", "value": 1},
                                            {"id": "custom.width", "value": 120}]},
                            {"matcher": {"id": "byName", "options": "Total"},
                             "properties": [{"id": "unit", "value": "decbytes"}, {"id": "decimals", "value": 1},
                                            {"id": "custom.cellOptions",
                                             "value": {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}},
                                            {"id": "custom.width", "value": 280}]},
                            {"matcher": {"id": "byName", "options": "MAC"},
                             "properties": [{"id": "custom.width", "value": 150}]},
                        ]},
    })


table("Devices by traffic, last 24 h",
      "Every device with application traffic, biggest first, with the application it used most. Filter a column "
      "with the funnel icon in its header, or pick devices with the Device selector at the top.",
      [target(f"sum by (name, mac) ({RX})", ref="A", fmt="table"),
       target(f"sum by (name, mac) ({TX})", ref="B", fmt="table"),
       target(f"topk by (mac) (1, sum by (mac, application) ({clean_app(RX)}) + "
              f"sum by (mac, application) ({clean_app(TX)}))", ref="C", fmt="table")],
      28, 12,
      {"excludeByName": {"Time": True, "Value #C": True},
       "indexByName": {"name": 0, "mac": 1, "application": 2, "Value #A": 3, "Value #B": 4, "Total": 5},
       "renameByName": {"name": "Device", "mac": "MAC", "application": "Top application",
                        "Value #A": "Downloaded", "Value #B": "Uploaded"}},
      "Total")

table("Who uses what, last 24 h",
      "Each device and application pair, biggest first. Filter Application (for example BitTorrent) to see which "
      "devices use it, or Device to see everything one device does.",
      [target(f"sum by (name, mac, application, category) ({clean_cat(clean_app(RX))})", ref="A", fmt="table"),
       target(f"sum by (name, mac, application, category) ({clean_cat(clean_app(TX))})", ref="B", fmt="table")],
      40, 14,
      {"excludeByName": {"Time": True},
       "indexByName": {"name": 0, "mac": 1, "application": 2, "category": 3, "Value #A": 4, "Value #B": 5, "Total": 6},
       "renameByName": {"name": "Device", "mac": "MAC", "application": "Application", "category": "Category",
                        "Value #A": "Downloaded", "Value #B": "Uploaded"}},
      "Total")


def var(name, label, query, multi=True):
    return {"allValue": ".*", "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
            "datasource": DS, "definition": query, "hide": 0, "includeAll": True, "multi": multi,
            "label": label, "name": name, "options": [],
            "query": {"query": query, "refId": "PrometheusVariableQueryEditor-VariableQuery"},
            "refresh": 2, "regex": "", "skipUrlSync": False, "sort": 1, "type": "query"}


dash = {
    "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                              "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
                              "name": "Annotations & Alerts", "type": "dashboard"}]},
    "description": "What the network's traffic is used for: applications, categories and devices, from UniFi's "
                   "traffic identification (DPI). UniFi reports each device's application traffic as a rolling "
                   "24-hour figure, so panels show the 24 hours up to the end of the selected time range. Needs DPI "
                   "on in UniFi and UP_UNIFI_CONTROLLER_0_SAVE_DPI=true for UnPoller.",
    "editable": True, "fiscalYearStartMonth": 0, "graphTooltip": 1, "liveNow": False,
    "links": [{"asDropdown": True, "icon": "external link", "includeVars": True, "keepTime": True,
               "tags": ["unifi-poller"], "title": "UniFi dashboards", "type": "dashboards"}],
    "panels": panels,
    "refresh": "5m", "schemaVersion": 39,
    "tags": ["unifi", "unifi-poller", "dpi"],
    "templating": {"list": [
        var("Controller", "Controller", "label_values(unpoller_client_dpi_receive_bytes, source)"),
        var("Site", "Site", 'label_values(unpoller_client_dpi_receive_bytes{source=~"$Controller"}, site_name)'),
        var("Device", "Device",
            'label_values(unpoller_client_dpi_receive_bytes{source=~"$Controller", site_name=~"$Site", mac!="TOTAL"}, name)'),
    ]},
    "time": {"from": "now-24h", "to": "now"},
    "timepicker": {}, "timezone": "", "title": "UniFi Applications (DPI)",
    "uid": "w3usaHLZk",  # same uid as the old DPI dashboard, so existing links and bookmarks keep working
    "version": 1, "weekStart": "",
}
json.dump(dash, open(OUT, "w"), indent=2)
print(f"{len(panels)} panels written to {OUT}")
