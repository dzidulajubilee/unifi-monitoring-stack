"""Small helpers for generating the Grafana dashboards in ../provisioning/dashboards.

Each dashboard is a Python file in this folder that builds a Dashboard and
saves it. Run `python3 grafana/build/<name>.py` and commit the JSON it writes.
Editing the JSON in Grafana works too, but the next generator run overwrites it.
"""
import json
from pathlib import Path

DS = {"type": "prometheus", "uid": "Prometheus"}
OUT = Path(__file__).resolve().parent.parent / "provisioning" / "dashboards"

# Validated categorical palette (dark steps), used in fixed order; one accent.
BLUE, ORANGE, AQUA, YELLOW, MAGENTA, GREEN, VIOLET, RED = (
    "#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767")
GREY = "#8a8984"
DOWN, UP = BLUE, ORANGE  # download / upload, everywhere

NEUTRAL = {"mode": "absolute", "steps": [{"color": "text", "value": None}]}


def steps(*pairs):
    """steps("text", 70, "orange", 90, "red") -> threshold steps."""
    out = [{"color": pairs[0], "value": None}]
    for value, color in zip(pairs[1::2], pairs[2::2], strict=False):
        out.append({"color": color, "value": value})
    return {"mode": "absolute", "steps": out}


def target(expr, legend="", ref="A", instant=False, fmt="time_series", interval=None, hide=False):
    t = {"datasource": DS, "editorMode": "code", "expr": expr, "legendFormat": legend, "refId": ref,
         "range": not instant, "instant": instant, "format": fmt}
    if interval:
        t["interval"] = interval
    if hide:
        t["hide"] = True
    return t


def color(name, col):
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": col}}]}


def color_regex(rx, col):
    return {"matcher": {"id": "byRegexp", "options": rx},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": col}}]}


def mirror(rx):
    """Draw matching series below the axis (uploads under downloads)."""
    return {"matcher": {"id": "byRegexp", "options": rx},
            "properties": [{"id": "custom.transform", "value": "negative-Y"}]}


class Dashboard:
    def __init__(self, title, uid, description, tags, file, time="now-24h", refresh="1m"):
        self.title, self.uid, self.description, self.tags, self.file = title, uid, description, tags, file
        self.time, self.refresh = time, refresh
        self.panels, self.vars = [], []
        self.y = 0          # next free row
        self._row_h = 0     # height of the row being filled
        self._x = 0
        self._row = None    # collapsed row collecting panels
        self._id = 0

    # ---------------------------------------------------------- layout
    def _place(self, w, h):
        if self._x + w > 24:
            self.newline()
        pos = {"x": self._x, "y": self.y, "w": w, "h": h}
        self._x += w
        self._row_h = max(self._row_h, h)
        return pos

    def newline(self):
        if self._x:
            self.y += self._row_h
        self._x, self._row_h = 0, 0

    def _add(self, p, w, h):
        p["gridPos"] = self._place(w, h)
        self._id += 1
        p["id"] = self._id
        p.setdefault("datasource", DS)
        (self._row["panels"] if self._row else self.panels).append(p)
        return p

    def row(self, title, collapsed=False):
        """Start a section. Collapsed rows hold their panels until opened."""
        self.newline()
        r = {"type": "row", "title": title, "collapsed": collapsed, "panels": [],
             "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}}
        self._id += 1
        r["id"] = self._id
        self.panels.append(r)
        self.y += 1
        self._row = r if collapsed else None
        return r

    # ---------------------------------------------------------- panels
    def stat(self, title, desc, expr, w=4, h=4, unit="none", decimals=None, thresholds=None,
             legend="", spark=False, calc="lastNotNull", mappings=None, color_mode="value", text_mode="value"):
        return self._add({
            "type": "stat", "title": title, "description": desc,
            "targets": [target(expr, legend, instant=not spark)],
            "options": {"colorMode": color_mode, "graphMode": "area" if spark else "none", "justifyMode": "auto",
                        "orientation": "auto", "textMode": text_mode, "wideLayout": True,
                        "showPercentChange": False,
                        "reduceOptions": {"calcs": [calc], "fields": "", "values": False}},
            "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "noValue": "–",
                                         "mappings": mappings or [], "color": {"mode": "thresholds"},
                                         "thresholds": thresholds or NEUTRAL}, "overrides": []},
        }, w, h)

    def timeseries(self, title, desc, targets, w=12, h=8, unit="none", decimals=None, min=0, max=None,
                   overrides=None, stack=False, legend="list", calcs=("mean", "max"), fill=10,
                   palette=True, draw="line", interp="smooth", thresholds=None, soft_max=None, time_from=None):
        custom = {"drawStyle": draw, "lineInterpolation": interp, "lineWidth": 1 if draw == "line" else 0,
                  "fillOpacity": fill, "gradientMode": "opacity" if draw == "line" else "none",
                  "showPoints": "never", "spanNulls": 300000, "axisPlacement": "auto",
                  "stacking": {"mode": "normal" if stack else "none", "group": "A"},
                  "thresholdsStyle": {"mode": "dashed" if thresholds else "off"}}
        if soft_max is not None:
            custom["axisSoftMax"] = soft_max
        p = {
            "type": "timeseries", "title": title, "description": desc, "targets": targets,
            "options": {"legend": {"displayMode": legend, "placement": "bottom" if legend == "list" else "right",
                                   "showLegend": legend != "hidden", "calcs": list(calcs) if legend == "table" else []},
                        "tooltip": {"mode": "multi", "sort": "desc"}},
            "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "min": min, "max": max,
                                         "mappings": [], "custom": custom,
                                         "color": {"mode": "palette-classic"} if palette else {"mode": "fixed", "fixedColor": BLUE},
                                         "thresholds": thresholds or NEUTRAL},
                            "overrides": overrides or []},
        }
        if time_from:
            p["timeFrom"] = time_from
        return self._add(p, w, h)

    def bars(self, title, desc, expr, legend, w=12, h=8, unit="none", decimals=None, fixed=BLUE,
             overrides=None, thresholds=None, max=None):
        return self._add({
            "type": "bargauge", "title": title, "description": desc,
            "targets": [target(expr, legend, instant=True)],
            "options": {"displayMode": "basic", "orientation": "horizontal", "namePlacement": "left",
                        "valueMode": "text", "showUnfilled": True, "sizing": "manual", "minVizHeight": 16,
                        "maxVizHeight": 22, "minVizWidth": 8,
                        "legend": {"showLegend": False, "displayMode": "list", "placement": "bottom", "calcs": []},
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
            "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "min": 0, "max": max, "mappings": [],
                                         "noValue": "No data",
                                         "color": {"mode": "thresholds" if thresholds else "fixed", "fixedColor": fixed},
                                         "thresholds": thresholds or NEUTRAL},
                            "overrides": overrides or []},
        }, w, h)

    def table(self, title, desc, targets, w=24, h=8, transformations=None, overrides=None, sort=None,
              filterable=True, footer=False):
        tr = list(transformations or [])
        if sort:
            tr.append({"id": "sortBy", "options": {"fields": {}, "sort": [{"field": sort[0], "desc": sort[1]}]}})
        return self._add({
            "type": "table", "title": title, "description": desc, "targets": targets, "transformations": tr,
            "options": {"cellHeight": "sm", "showHeader": True,
                        "footer": {"show": footer, "reducer": ["sum"], "fields": "", "countRows": False}},
            "fieldConfig": {"defaults": {"mappings": [], "color": {"mode": "thresholds"},
                                         "custom": {"align": "auto", "filterable": filterable, "inspect": False,
                                                    "cellOptions": {"type": "auto"}},
                                         "thresholds": NEUTRAL},
                            "overrides": overrides or []},
        }, w, h)

    def text(self, content, w=24, h=3):
        return self._add({"type": "text", "title": "", "transparent": True,
                          "options": {"mode": "markdown", "content": content}}, w, h)

    # ---------------------------------------------------------- variables
    def var(self, name, query, label=None, multi=True, include_all=True, hide=0, all_value=".*"):
        """all_value=None makes "All" the list of current options instead of a catch-all regex,
        so it only matches what the variable lists (e.g. gateways, not every device)."""
        self.vars.append({
            "allValue": all_value, "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
            "datasource": DS, "definition": query, "hide": hide, "includeAll": include_all, "multi": multi,
            "label": label or name, "name": name, "options": [],
            "query": {"query": query, "refId": "PrometheusVariableQueryEditor-VariableQuery"},
            "refresh": 2, "regex": "", "skipUrlSync": False, "sort": 1, "type": "query"})

    def choice(self, name, options, selected, label=None):
        """A fixed list to pick from: options is [(text, value)], selected the values ticked by default."""
        opts = [{"selected": v in selected, "text": t, "value": v} for t, v in options]
        self.vars.append({
            "current": {"selected": True, "text": [t for t, v in options if v in selected], "value": list(selected)},
            "hide": 0, "includeAll": False, "multi": True, "label": label or name, "name": name, "options": opts,
            "query": ",".join(f"{t} : {v}" for t, v in options), "skipUrlSync": False, "type": "custom"})

    # ---------------------------------------------------------- output
    def save(self):
        d = {
            "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                                      "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
                                      "name": "Annotations & Alerts", "type": "dashboard"}]},
            "description": self.description, "editable": True, "fiscalYearStartMonth": 0, "graphTooltip": 1,
            "liveNow": False,
            "links": [{"asDropdown": True, "icon": "external link", "includeVars": True, "keepTime": True,
                       "tags": ["unifi-poller"], "title": "UniFi dashboards", "type": "dashboards"}],
            "panels": self.panels, "refresh": self.refresh, "schemaVersion": 39, "tags": self.tags,
            "templating": {"list": self.vars}, "time": {"from": self.time, "to": "now"}, "timepicker": {},
            "timezone": "", "title": self.title, "uid": self.uid, "version": 1, "weekStart": "",
        }
        path = OUT / self.file
        path.write_text(json.dumps(d, indent=2) + "\n")
        n = sum(1 + len(p.get("panels", [])) for p in self.panels)
        print(f"{self.title}: {n} panels -> {path}")
