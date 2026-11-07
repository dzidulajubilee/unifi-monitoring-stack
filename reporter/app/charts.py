"""Inline SVG charts for the PDF."""

import datetime as dt
import html
import math

from . import config
from .text import fbits


def svg_minibars(m, w=196, h=78):
    """Small bar chart for one trend: earlier periods grey, this period blue, gaps dashed."""
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
                f'fill="{"#2a78d6" if last else "#c9c8c2"}"/>'
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


def svg_chart(down, up, kind, start, end, w=658, h=236):
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
    g.append(f'<path d="{path(down)}" fill="none" stroke="#2a78d6" stroke-width="1.6" stroke-linejoin="round"/>')
    # direct label at the peak of download
    pt, pv = max(down, key=lambda p: p[1])
    px, py = x(pt), y(pv)
    when = dt.datetime.fromtimestamp(pt, config.TZ)
    anchor = "end" if px > L + pw * 0.75 else "start"
    dx = -8 if anchor == "end" else 8
    g.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="#2a78d6" stroke="#ffffff" stroke-width="2"/>')
    g.append(
        f'<text x="{px + dx:.1f}" y="{py + 4:.1f}" font-family="Plex" font-weight="500" font-size="7.5pt" fill="#1b1b1a" text-anchor="{anchor}">'
        f"Peak {fbits(pv)}, {when:%a %H:%M}</text>"
    )
    return (
        f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
        f'aria-label="Internet throughput, download and upload">{"".join(g)}</svg>'
    )
