"""Builds a report: collects the data once, renders the full and summary PDFs."""

import datetime as dt

from jinja2 import Environment, FileSystemLoader

from . import config
from .charts import svg_chart, svg_minibars
from .collect import (
    collect,
    collect_apps,
    collect_isp,
    collect_maint,
    collect_trends,
    collect_wifi,
    events,
    threat_list,
    wan_series,
)
from .config import OUT, STYLES, load_settings, log
from .links import SECTION_DASHBOARD, grafana_linker
from .periods import KINDS, period_end, period_label, period_start, report_path
from .sources import Source
from .text import attention, change, fbits, fbytes, fduration, fnum, fpct, summary, trend_change


def build(kind, end=None, start=None):
    st = load_settings()  # applies the time zone before any dates are worked out
    end = end or period_end(kind, dt.datetime.now(config.TZ))
    start = start or period_start(kind, end)
    prev_start = period_start(kind, start, end - start)
    word = KINDS[kind]
    title, dates = period_label(kind, start, end)
    name = st["general"]["name"].strip()
    # what each version shows; data is collected for anything ticked in either
    show = {
        "full": {k for k, v in st["sections"].items() if v},
        "summary": {k for k, v in st["sections_summary"].items() if v},
    }
    on = show["full"] | show["summary"]
    log.info(
        "building %s report %s (%s), full: %s, summary: %s",
        kind,
        title,
        dates,
        ",".join(sorted(show["full"])),
        ",".join(sorted(show["summary"])),
    )

    cur, prev = Source(start, end), Source(prev_start, start)
    d, p = collect(cur), collect(prev)
    d["_days"] = (end - start).days
    d["wifi"] = collect_wifi(cur) if "wifi" in on else None
    d["dpi"] = collect_apps(cur) if "apps" in on else None
    d["isp"] = collect_isp(cur) if "isp" in on else None
    d["maint"] = collect_maint(cur) if "maintenance" in on else None
    d["trends"] = collect_trends(kind, start) if "trends" in on else None
    d["_word"] = word
    cov = prev.coverage()
    gap = 1 - cur.coverage()["prom"]
    covered = {k: v >= 0.95 for k, v in cov.items()}
    down, up, step = wan_series(cur, kind) if "throughput" in on else ([], [], 0)
    ch = lambda key, src="prom": change(d.get(key), p.get(key), "", word, covered[src])

    def headline(shown):
        """Headline numbers follow the sections shown; if none match, show them all."""
        figs = [
            ("Downloaded", fbytes(d["wan_down"]), ch("wan_down"), {"downloaded"}),
            ("Peak devices online", fnum(d["clients_peak"]), ch("clients_peak"), {"devices", "aps"}),
            ("Blocked from the internet", fnum(d["blocks_in"]), ch("blocks_in", "loki"), {"security"}),
            ("Threats blocked", fnum(d["threats_blocked"]), ch("threats_blocked", "loki"), {"security", "threats"}),
        ]
        if "downloaded" not in shown:
            figs = figs[1:]  # never show it when unticked, even in the show-all fallback
        return [f[:3] for f in figs if f[3] & shown] or [f[:3] for f in figs]

    env = Environment(loader=FileSystemLoader(config.ROOT / "templates"), autoescape=True)
    env.filters.update(bytes=fbytes, bits=fbits, num=fnum, pct=fpct, duration=fduration)
    # The summary sentences and attention list cover every section ticked in either version, so
    # the summary still warns about, say, an access point problem when it leaves out the Wi-Fi pages.
    lines, att = summary(d, p, word, covered, on), attention(d, on)
    evts = events(cur, wan="isp" not in on) if "events" in on else []
    thr = threat_list(cur) if "threats" in on else ([], 0)
    chart = svg_chart(down, up, kind, start, end) if "throughput" in on else ""
    chart_short = (
        svg_chart(down, up, kind, start, end, h=140) if "throughput" in on else ""
    )  # keeps the summary on one page
    from weasyprint import HTML  # imported late: slow to load, not needed until a report is due

    OUT.mkdir(parents=True, exist_ok=True)
    pdfs = {}
    for style in STYLES:
        shown = show[style]
        page1 = bool(shown & {"summary", "figures", "throughput"})
        html_out = env.get_template("report.html").render(
            name=name,
            on=shown,
            style=style,
            figs=headline(shown),
            page1=page1,
            aps_break=page1 or bool(shown & {"internet", "devices"}),
            kind=kind,
            word=word,
            title=title,
            dates=dates,
            tzname=str(config.TZ),
            generated=dt.datetime.now(config.TZ),
            d=d,
            p=p,
            ch=ch,
            covered=covered,
            summary=lines,
            attention=att,
            events=evts,
            threats=thr,
            chart=chart if style == "full" else chart_short,
            fonts=(config.ROOT / "fonts").as_uri(),
            trend_change=trend_change,
            minibars=svg_minibars,
            gf=grafana_linker(st, start, end),
            section_dashboard=SECTION_DASHBOARD,
            errors=cur.errors + prev.errors + (d["trends"]["errors"] if d["trends"] else []),
            gap=gap,
            max_client=max([c["bytes"] for c in d["top_clients"]] or [1]),
        )
        pdf = report_path(kind, start, end, style)
        tmp = pdf.with_suffix(".part")
        HTML(string=html_out, base_url=str(config.ROOT)).write_pdf(tmp)
        tmp.replace(pdf)  # appears in the portal only once complete
        pdfs[style] = pdf
    log.info("wrote %s and its summary (%d query errors)", pdfs["full"], len(cur.errors + prev.errors))
    what = f"{kind.capitalize()} report, {title}" if kind != "custom" else f"Report for {title}"
    subject = f"{name}: {what} ({dates})" if name else f"{what} ({dates})"
    return pdfs, subject, lines, len(cur.errors + prev.errors)
