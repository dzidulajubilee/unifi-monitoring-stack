"""Inline SVG charts for the PDF."""

import colorsys
import datetime as dt
import html
import math

from . import config
from .text import fbits

ACCENT = config.THEMES["blue"]["accent"]


def svg_minibars(m, w=196, h=78, accent=ACCENT):
    """Small bar chart for one trend: earlier periods grey, this period in the accent, gaps dashed."""
    vals, labels = m["values"], m["labels"]
    n = len(vals)
    top = max([v for v in vals if v is not None] or [0]) or 1
    if m["unit"] == "pct":
        top = max(top, 1.0)
    L, B, T = 2, 14, 4
    gap = 2
    bw = (w - L * 2 - gap * (n - 1)) / n
    ph = h - B - T
    g = [
        f'<line x1="0" x2="{w}" y1="{T + ph:.1f}" y2="{T + ph:.1f}" stroke="#1b1b1a" stroke-opacity="0.18" stroke-width="1"/>'
    ]
    for i, (v, lbl) in enumerate(zip(vals, labels, strict=False)):
        x = L + i * (bw + gap)
        last = i == n - 1
        if v is None:
            g.append(
                f'<rect x="{x:.1f}" y="{T + ph - 10:.1f}" width="{bw:.1f}" height="10" fill="none" '
                f'stroke="#a9a8a2" stroke-width="0.8" stroke-dasharray="2 2" rx="1.5"/>'
            )
        else:
            bh = max(1.0, v / top * ph)
            g.append(
                f'<rect x="{x:.1f}" y="{T + ph - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="1.5" '
                f'fill="{accent if last else "#c9c8c2"}"/>'
            )
        if n <= 8 or i in (0, n - 1):
            g.append(
                f'<text x="{x + bw / 2:.1f}" y="{h - 3}" text-anchor="middle" font-family="PlexMono" font-size="5.5pt" '
                f'fill="{"#1b1b1a" if last else "#8a8984"}">{html.escape(lbl)}</text>'
            )
    return (
        f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="{html.escape(m["title"])} by period">'
        + "".join(g)
        + "</svg>"
    )


def svg_chart(down, up, kind, start, end, w=658, h=236, accent=ACCENT):
    """WAN throughput line chart as inline SVG (two series, one shared axis)."""
    if not down:
        return '<p class="muted">No throughput data for this period</p>'
    L, R, T, B = 44, 12, 28, 28
    pw, ph = w - L - R, h - T - B
    t0, t1 = start.timestamp(), end.timestamp()
    vmax = max(v for _, v in down + up) or 1
    # nice axis maximum in Mbps
    mb = vmax / 1e6
    stepv = 10 ** math.floor(math.log10(mb / 4)) if mb > 0 else 1
    for m in (1, 2, 2.5, 5, 10):
        if mb / (stepv * m) <= 4:
            stepv *= m
            break
    top = stepv * math.ceil(mb / stepv) * 1e6
    x = lambda t: L + (t - t0) / (t1 - t0) * pw
    y = lambda v: T + ph - v / top * ph
    path = lambda pts: "M" + " L".join(f"{x(t):.1f},{y(v):.1f}" for t, v in pts)
    g = []
    # grid + y labels
    v = 0.0
    while v <= top + 1:
        yy = y(v)
        g.append(
            f'<line x1="{L}" x2="{w - R}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="#1b1b1a" stroke-opacity="0.08" stroke-width="1"/>'
        )
        g.append(
            f'<text x="{L - 8}" y="{yy + 3:.1f}" font-family="PlexMono" font-size="7pt" fill="#8a8984" text-anchor="end">{v / 1e6:.0f}</text>'
        )
        v += stepv * 1e6
    g.append(
        f'<text x="{L - 8}" y="{T - 14}" font-family="PlexMono" font-size="7pt" fill="#8a8984" text-anchor="end">Mbps</text>'
    )
    # x labels
    day = start
    while day < end:
        xx = x(day.timestamp())
        if (end - start).days <= 10:
            g.append(
                f'<line x1="{xx:.1f}" x2="{xx:.1f}" y1="{T}" y2="{T + ph}" stroke="#1b1b1a" stroke-opacity="0.08" stroke-width="1"/>'
            )
            g.append(
                f'<text x="{xx + pw / max(1, (end - start).days) / 2:.1f}" y="{h - 8}" font-family="PlexMono" font-size="7pt" fill="#8a8984" text-anchor="middle">{day:%a %-d}</text>'
            )
            day += dt.timedelta(days=1)
        else:
            stride = 7 if (end - start).days <= 45 else 14
            g.append(
                f'<text x="{xx:.1f}" y="{h - 8}" font-family="PlexMono" font-size="7pt" fill="#8a8984" text-anchor="middle">{day:%-d %b}</text>'
            )
            day += dt.timedelta(days=stride)
    # series: download leads in the accent, upload sits in a quiet neutral
    g.append(f'<path d="{path(up)}" fill="none" stroke="#a9a8a2" stroke-width="1.4" stroke-linejoin="round"/>')
    g.append(f'<path d="{path(down)}" fill="none" stroke="{accent}" stroke-width="1.6" stroke-linejoin="round"/>')
    # direct label at the peak of download
    pt, pv = max(down, key=lambda p: p[1])
    px, py = x(pt), y(pv)
    when = dt.datetime.fromtimestamp(pt, config.TZ)
    anchor = "end" if px > L + pw * 0.75 else "start"
    dx = -8 if anchor == "end" else 8
    g.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="{accent}" stroke="#ffffff" stroke-width="2"/>')
    g.append(
        f'<text x="{px + dx:.1f}" y="{py + 4:.1f}" font-family="Plex" font-weight="500" font-size="7.5pt" fill="#1b1b1a" text-anchor="{anchor}">'
        f"Peak {fbits(pv)}, {when:%a %H:%M}</text>"
    )
    return (
        f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
        f'aria-label="Internet throughput, download and upload">{"".join(g)}</svg>'
    )


def svg_dns(rows, step, start, end, w=658, h=150, accent=ACCENT):
    """DNS queries per hour or day: the bar is all queries (neutral), the blocked part sits in the accent."""
    if not any(t is not None for _, t, _ in rows):
        return '<p class="muted">No query history for this period</p>'
    L, R, T, B = 44, 12, 18, 22
    pw, ph = w - L - R, h - T - B
    vmax = max(t or 0 for _, t, _ in rows) or 1
    stepv = 10 ** math.floor(math.log10(vmax / 3)) if vmax >= 3 else 1
    for m in (1, 2, 2.5, 5, 10):
        if vmax / (stepv * m) <= 3:
            stepv *= m
            break
    top = stepv * math.ceil(vmax / stepv)
    fmt = lambda v: f"{v / 1e6:g}M" if v >= 1e6 else f"{v / 1e3:g}k" if v >= 1e3 else f"{v:g}"
    y = lambda v: T + ph - v / top * ph
    n = len(rows)
    slot = pw / n
    gap = min(2.0, slot * 0.25)
    g = []
    v = 0.0
    while v <= top + 1e-9:
        yy = y(v)
        g.append(
            f'<line x1="{L}" x2="{w - R}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="#1b1b1a" stroke-opacity="0.08" stroke-width="1"/>'
        )
        g.append(
            f'<text x="{L - 8}" y="{yy + 3:.1f}" font-family="PlexMono" font-size="7pt" fill="#8a8984" text-anchor="end">{fmt(v)}</text>'
        )
        v += stepv
    for i, (_t, tot, blk) in enumerate(rows):
        x = L + i * slot + gap / 2
        bw = max(0.6, slot - gap)
        if tot:
            g.append(
                f'<rect x="{x:.1f}" y="{y(tot):.1f}" width="{bw:.1f}" height="{T + ph - y(tot):.1f}" fill="#c9c8c2"/>'
            )
        if blk:
            g.append(
                f'<rect x="{x:.1f}" y="{y(blk):.1f}" width="{bw:.1f}" height="{T + ph - y(blk):.1f}" fill="{accent}"/>'
            )
    # x labels: every 6 hours for hourly bars, every day for short periods, then every week or fortnight
    if step == 3600:
        t = start
        while t < end:
            xx = L + (t.timestamp() - start.timestamp()) / (end.timestamp() - start.timestamp()) * pw
            g.append(
                f'<text x="{xx:.1f}" y="{h - 6}" font-family="PlexMono" font-size="7pt" fill="#8a8984" '
                f'text-anchor="start">{t:%H:%M}{f" {t:%a}" if t.hour == 0 else ""}</text>'
            )
            t += dt.timedelta(hours=6)
        return (
            f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
            f'aria-label="DNS queries per hour, blocked part highlighted">{"".join(g)}</svg>'
        )
    days = (end - start).days
    every = 1 if days <= 10 else 7 if days <= 45 else 14
    day = start
    while day < end:
        xx = L + (day.timestamp() - start.timestamp()) / (end.timestamp() - start.timestamp()) * pw
        label = f"{day:%a %-d}" if every == 1 else f"{day:%-d %b}"
        anchor = "middle" if every == 1 else "start"
        off = pw / max(1, days) / 2 if every == 1 else 0
        g.append(
            f'<text x="{xx + off:.1f}" y="{h - 6}" font-family="PlexMono" font-size="7pt" fill="#8a8984" text-anchor="{anchor}">{label}</text>'
        )
        day += dt.timedelta(days=every)
    unit = "hour" if step == 3600 else "day"
    return (
        f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
        f'aria-label="DNS queries per {unit}, blocked part highlighted">{"".join(g)}</svg>'
    )


# Categorical palette for networks: eight hues checked for colour-blind and normal-vision separation on white
# (slot 1 is the report's blue; net_palette puts the chosen accent there). Three are light against white, so the table beside the chart names each one.
NET_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]


def _hue(c):
    h, _, s = colorsys.rgb_to_hls(*(int(c[i : i + 2], 16) / 255 for i in (1, 3, 5)))
    return h * 360, s


def net_palette(accent=ACCENT):
    """NET_COLORS led by the report's accent. Hues within 30 degrees of a coloured accent are left out, so no
    other network looks like the first one; a grey accent (Graphite) keeps them all."""
    ah, sat = _hue(accent)
    rest = [c for c in NET_COLORS if c != accent]
    if sat > 0.2:
        rest = [c for c in rest if min(abs(_hue(c)[0] - ah), 360 - abs(_hue(c)[0] - ah)) > 30]
    return [accent] + rest


def net_color(slot, palette=NET_COLORS):
    return palette[slot % len(palette)]


def svg_networks(rows, series, step, start, end, w=658, h=190, palette=NET_COLORS):
    """Devices online per network over the period: one thin line per network on the report's usual grid."""
    times = sorted({t for r in rows for t, _ in series.get(r["key"], [])})
    if not times:
        return '<p class="muted">No device data for this period</p>'
    L, R, T, B = 44, 12, 12, 22
    pw, ph = w - L - R, h - T - B
    t0, t1 = start.timestamp(), end.timestamp()
    top_v = max((v for r in rows for _, v in series.get(r["key"], [])), default=0) or 1
    stepv = 10 ** math.floor(math.log10(top_v / 3)) if top_v >= 3 else 1
    for m in (1, 2, 2.5, 5, 10):
        if top_v / (stepv * m) <= 3:
            stepv *= m
            break
    top = stepv * math.ceil(top_v / stepv)
    x = lambda t: L + ((t - step / 2) - t0) / (t1 - t0) * pw  # a point averages the step before it
    y = lambda v: T + ph - v / top * ph
    g = []
    v = 0.0
    while v <= top + 1e-9:
        g.append(
            f'<line x1="{L}" x2="{w - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#1b1b1a" stroke-opacity="0.08" stroke-width="1"/>'
        )
        g.append(
            f'<text x="{L - 8}" y="{y(v) + 3:.1f}" font-family="PlexMono" font-size="7pt" fill="#8a8984" text-anchor="end">{v:g}</text>'
        )
        v += stepv
    for r in sorted(rows, key=lambda r: r["slot"]):
        pts = series.get(r["key"], [])
        # break the line where there's no data rather than drawing across the gap
        path, last = [], None
        for t, val in pts:
            path.append(("M" if last is None or t - last > step * 1.5 else "L") + f"{x(t):.1f},{y(val):.1f}")
            last = t
        if path:
            g.append(
                f'<path d="{" ".join(path)}" fill="none" stroke="{net_color(r["slot"], palette)}" stroke-width="1.4" '
                'stroke-linejoin="round" stroke-linecap="round"/>'
            )
    days = (end - start).days
    every = 1 if days <= 10 else 7 if days <= 45 else 14
    day = start
    while day < end:
        xx = L + (day.timestamp() - t0) / (t1 - t0) * pw
        if every == 1:
            g.append(
                f'<line x1="{xx:.1f}" x2="{xx:.1f}" y1="{T}" y2="{T + ph}" stroke="#1b1b1a" stroke-opacity="0.08" stroke-width="1"/>'
            )
        label, anchor = (f"{day:%a %-d}", "middle") if every == 1 else (f"{day:%-d %b}", "start")
        off = pw / max(1, days) / 2 if every == 1 else 0
        g.append(
            f'<text x="{xx + off:.1f}" y="{h - 6}" font-family="PlexMono" font-size="7pt" fill="#8a8984" text-anchor="{anchor}">{label}</text>'
        )
        day += dt.timedelta(days=every)
    return (
        f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
        f'aria-label="Devices online by network over the period">{"".join(g)}</svg>'
    )
