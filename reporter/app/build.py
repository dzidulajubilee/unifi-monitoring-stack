"""Builds a report: collects the data once, renders the full and summary PDFs."""

import datetime as dt

from jinja2 import Environment, FileSystemLoader

from . import config, dns
from .charts import net_color, svg_chart, svg_dns, svg_minibars, svg_networks
from .collect import (
    client_names,
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
from .text import (
    attention,
    change,
    delta,
    dns_comparable,
    dns_pairs,
    fbits,
    fbytes,
    fduration,
    fnum,
    fpct,
    points_change,
    share,
    summary,
    trend_change,
)


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
    hidden = {n.strip().lower() for n in st["general"].get("hidden_networks", "").split(",") if n.strip()}
    for x in (d, p):  # e.g. a network that only holds infrastructure
        x["networks"] = [n for n in x.get("networks") or [] if (n["key"] or "").lower() not in hidden]
    # the network with the most devices on average gets the report's blue; the others keep a colour by name
    if d["networks"]:
        lead = max(d["networks"], key=lambda n: (n["avg"] or 0, n["key"]))
        names = sorted(n["key"] for n in d["networks"] if n is not lead)
        for n in d["networks"]:
            n["slot"] = 0 if n is lead else 1 + names.index(n["key"])
    net_chart = (
        svg_networks(d["networks"], d.get("net_series") or {}, d.get("net_step", 3600), start, end)
        if "devices" in on and d["networks"]
        else ""
    )
    gap = 1 - cur.coverage()["prom"]
    d["dns"] = p["dns"] = None
    if "dns" in on and dns.servers(st):
        d["dns"], p["dns"] = [], []
        # devices are only named when UniFi data covers the period, or an address could look unshared
        names = client_names(cur) if gap <= 0.05 else {}
        d["dns_named"] = bool(names)
        for srv in dns.servers(st):
            c, before = dns.read(srv, start, end, prev_start)
            c["top_clients"] = dns.name_clients(c["top_clients"], names)
            rows, c["step"] = dns.buckets(c["series"], start, end)
            c["chart"] = svg_dns(rows, c["step"], start, end)
            c["series"] = []  # only the chart needs it
            d["dns"].append(c)
            p["dns"].append(before)
        d["_dns_prev"] = p["dns"]  # attention() only sees d
    cov = prev.coverage()
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
    att = attention(d, on)

    def sentences(style):
        """Summary sentences for one version: everything ticked anywhere, except that the download
        sentence follows that version's own Data downloaded switch."""
        return summary(d, p, word, covered, on if "downloaded" in show[style] else on - {"downloaded"})

    lines = {style: sentences(style) for style in STYLES}
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
        html_out = env.get_template("report.html").render(
            name=name,
            on=shown,
            style=style,
            figs=headline(shown),
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
            summary=lines[style],
            attention=att,
            events=evts,
            threats=thr,
            chart=chart if style == "full" else chart_short,
            fonts=(config.ROOT / "fonts").as_uri(),
            trend_change=trend_change,
            dns_pairs=dns_pairs,
            net_chart=net_chart,
            net_color=net_color,
            dns_comparable=dns_comparable,
            dns_change=change,
            ch_value=lambda cur, prev: delta(cur, prev, covered["prom"]),
            share=share,
            points_change=points_change,
            tz=config.TZ,
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
    # the email body uses the full version's sentences
    return pdfs, subject, lines["full"], len(cur.errors + prev.errors)
