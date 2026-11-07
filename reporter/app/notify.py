"""Portal notifications and their per-user read state."""

import datetime as dt
import json
import os
import threading

from . import config
from .config import OUT, log

# What happened (reports made, emails sent or failed, settings changed), shown
# in the portal's notification list. Read state is kept per user.
NOTES = OUT / "notifications.json"


READS = OUT / "notifications-read.json"
CLEARED = OUT / "notifications-cleared.json"  # per user, like READS


_notes_lock = threading.Lock()


def _read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return default


def _write_json(path, data):
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def notify(text, level="info", by=None, file=None, admins_only=False, for_user=None):
    """level: info or error. admins_only notes are shown to admins and to for_user."""
    note = {
        "id": os.urandom(6).hex(),
        "t": dt.datetime.now(config.TZ).isoformat(),
        "text": text,
        "level": level,
        "by": by,
        "file": file,
        "admins_only": admins_only,
        "for_user": for_user,
    }
    with _notes_lock:
        notes = _read_json(NOTES, [])
        notes.append(note)
        _write_json(NOTES, notes[-300:])
    log.info("notification: %s", text)


def notes_for(login, admin):
    """Newest first, with a read flag for this user."""
    with _notes_lock:
        notes = _read_json(NOTES, [])
        read = set(_read_json(READS, {}).get(login, []))
        cleared = set(_read_json(CLEARED, {}).get(login, []))
    out = []
    for n in reversed(notes):
        if n["id"] in cleared or (n.get("admins_only") and not admin and n.get("for_user") != login):
            continue
        out.append({**n, "read": n["id"] in read})
    return out


def _remember(path, login, ids):
    """Add ids to this user's list in path, forgetting ids of notes that have been pruned."""
    with _notes_lock:
        state = _read_json(path, {})
        live = {n["id"] for n in _read_json(NOTES, [])}
        state[login] = sorted((set(state.get(login, [])) | set(ids)) & live)
        _write_json(path, state)


def mark_read(login, ids):
    _remember(READS, login, ids)


def clear(login, ids):
    """Hide notifications for this user only; others still see them."""
    _remember(CLEARED, login, ids)
