"""What the reporter has made: the periods the schedule has sent, and who made each saved report.

The schedule keeps its own record instead of checking whether a PDF exists, so a report someone makes by hand
before the send hour doesn't stop the email, and deleting a report doesn't make the schedule send it again.
"""

import json
import threading

from .config import OUT

SENT = OUT / "scheduled.json"  # {kind: end of the latest period the schedule sent, e.g. "2026-11-09"}
MADE = OUT / "made.json"  # {file name: {"by": "schedule" or a name, "remade": bool}}
_lock = threading.Lock()


def _read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _write(path, data):
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


def sent_periods():
    """{kind: end date} of the latest period the schedule has sent, or None before the first record."""
    with _lock:
        return _read(SENT) if SENT.exists() else None


def start_record(periods):
    """First run after an upgrade: count the periods whose PDF already exists as sent."""
    with _lock:
        _write(SENT, {k: f"{e:%Y-%m-%d}" for k, e in periods.items()})


def mark_sent(kind, end):
    with _lock:
        s = _read(SENT)
        s[kind] = f"{end:%Y-%m-%d}"
        _write(SENT, s)


def record_made(name, by, remade):
    with _lock:
        m = _read(MADE)
        m[name] = {"by": by, "remade": bool(remade)}
        _write(MADE, m)


def forget(name):
    with _lock:
        m = _read(MADE)
        if m.pop(name, None) is not None:
            _write(MADE, m)


def made():
    with _lock:
        return _read(MADE)
