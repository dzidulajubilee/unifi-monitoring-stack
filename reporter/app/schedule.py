"""Which scheduled reports are due."""

import datetime as dt

from . import config
from .config import OUT, load_settings
from .periods import period_end, period_start, report_path


def due_reports(now=None):
    """Scheduled reports whose period has closed, it's past the send hour, and no
    PDF exists yet. Only periods ending after the scheduler first started count,
    so a fresh install doesn't back-fill old periods it has little data for."""
    st = load_settings()["schedule"]  # first: applies the time zone
    now = now or dt.datetime.now(config.TZ)
    if not st.get("enabled"):
        return []
    OUT.mkdir(parents=True, exist_ok=True)
    since_file = OUT / ".scheduler-since"
    if not since_file.exists():
        since_file.write_text(now.isoformat())
    since = dt.datetime.fromisoformat(since_file.read_text().strip())
    due = []
    for kind in st.get("kinds", []):
        if kind not in ("weekly", "monthly", "quarterly"):
            continue
        end = period_end(kind, now)
        if (
            end > since
            and now.hour >= int(st.get("hour", 7))
            and not report_path(kind, period_start(kind, end)).exists()
        ):
            due.append((kind, end))
    return due
