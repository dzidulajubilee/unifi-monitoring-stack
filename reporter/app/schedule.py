"""Which scheduled reports are due."""

import datetime as dt

from . import config
from .config import OUT, load_settings
from .history import sent_periods, start_record
from .periods import period_end, period_start, report_path


def due_reports(now=None):
    """Scheduled reports whose period has closed, it's past the send hour, and the
    schedule hasn't sent that period yet (history.SENT; a PDF made by hand doesn't
    count). Only periods ending after the scheduler first started count, so a fresh
    install doesn't back-fill old periods it has little data for."""
    st = load_settings()["schedule"]  # first: applies the time zone
    now = now or dt.datetime.now(config.TZ)
    if not st.get("enabled"):
        return []
    OUT.mkdir(parents=True, exist_ok=True)
    since_file = OUT / ".scheduler-since"
    if not since_file.exists():
        since_file.write_text(now.isoformat())
    since = dt.datetime.fromisoformat(since_file.read_text().strip())
    sent = sent_periods()
    if sent is None:  # first run with the record: periods already on disk were sent the old way
        latest = {k: period_end(k, now) for k in ("weekly", "monthly", "quarterly")}
        start_record({k: e for k, e in latest.items() if report_path(k, period_start(k, e)).exists()})
        sent = sent_periods()
    due = []
    for kind in st.get("kinds", []):
        if kind not in ("weekly", "monthly", "quarterly"):
            continue
        end = period_end(kind, now)
        if end > since and now.hour >= int(st.get("hour", 7)) and sent.get(kind) != f"{end:%Y-%m-%d}":
            due.append((kind, end))
    return due
