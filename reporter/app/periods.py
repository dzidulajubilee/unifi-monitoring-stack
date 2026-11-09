"""Report periods: weeks, months, quarters, custom ranges, and report file names."""

import datetime as dt
import re
from pathlib import Path

from . import config
from .config import OUT

KINDS = {"weekly": "week", "monthly": "month", "quarterly": "quarter", "custom": "period"}


def period_end(kind, now):
    """Start of the current (unfinished) period = end of the last closed one."""
    d = now.astimezone(config.TZ).date()
    if kind == "weekly":
        d -= dt.timedelta(days=d.weekday())
    elif kind == "monthly":
        d = d.replace(day=1)
    else:
        d = d.replace(month=(d.month - 1) // 3 * 3 + 1, day=1)
    return dt.datetime.combine(d, dt.time(), config.TZ)


def period_start(kind, end, length=None):
    if kind == "custom":
        return end - length
    if kind == "weekly":
        return end - dt.timedelta(days=7)
    months = 1 if kind == "monthly" else 3
    m = end.month - months
    y = end.year + (m - 1) // 12
    return end.replace(year=y, month=(m - 1) % 12 + 1)


def period_label(kind, start, end):
    last = end - dt.timedelta(days=1)
    if kind == "weekly":
        return (f"Week {start.isocalendar().week}", f"{start:%-d %b} – {last:%-d %b %Y}")
    if kind == "monthly":
        return f"{start:%B %Y}", f"{start:%-d %b} – {last:%-d %b %Y}"
    if kind == "custom":
        days = max(1, round((end - start).total_seconds() / 86400))
        if start.date() == last.date():
            return f"{start:%-d %b %Y}", "1 day"
        return f"{start:%-d %b} – {last:%-d %b %Y}", f"{days} days"
    return (f"Q{(start.month - 1) // 3 + 1} {start.year}", f"{start:%b} – {last:%b %Y}")


TREND_COUNT = {"weekly": 8, "monthly": 6, "quarterly": 4}


def trend_periods(kind, start):
    """The report's own period and the ones before it, oldest first: [(start, end, label)]."""
    if kind not in TREND_COUNT:
        return []
    out, s_ = [], start
    for _ in range(TREND_COUNT[kind]):
        e_ = _next_period(kind, s_)
        if kind == "weekly":
            label = f"W{s_.isocalendar().week}"
        elif kind == "monthly":
            label = f"{s_:%b}"
        else:
            label = f"Q{(s_.month - 1) // 3 + 1} {s_:%y}"
        out.append((s_, e_, label))
        s_ = period_start(kind, s_)
    return out[::-1]


def report_title(kind, pdf_or_start, end=None):
    """'Weekly Network Report, Week 44 (26 Oct – 1 Nov 2026)' from a report file, or from a period start/end."""
    if isinstance(pdf_or_start, Path):
        m = re.match(r"\w+-(\d{4}-\d{2}-\d{2})(?:-to-(\d{4}-\d{2}-\d{2}))?(?:-summary)?\.pdf$", pdf_or_start.name)
        day = lambda x: dt.datetime.combine(dt.date.fromisoformat(x), dt.time(), config.TZ)
        start = day(m[1])
        end = day(m[2]) + dt.timedelta(days=1) if m[2] else _next_period(kind, start)
    else:
        start = pdf_or_start
    title, dates = period_label(kind, start, end)
    if kind == "custom":
        return f"Network Report for {title}"
    return f"{kind.capitalize()} Network Report, {title} ({dates})"


def _next_period(kind, start):
    if kind == "weekly":
        return start + dt.timedelta(days=7)
    months = 1 if kind == "monthly" else 3
    m = start.month + months
    return start.replace(year=start.year + (m - 1) // 12, month=(m - 1) % 12 + 1)


def report_path(kind, start, end=None, style="full"):
    stem = f"{kind}-{start:%Y-%m-%d}"
    if kind == "custom":
        stem += f"-to-{end - dt.timedelta(days=1):%Y-%m-%d}"
    if style == "summary":
        stem += "-summary"
    return OUT / f"{stem}.pdf"
