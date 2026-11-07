"""Command line: build a report now."""

import argparse
import datetime as dt
import logging
import sys

from . import config
from .build import build
from .config import load_settings
from .mail import deliver, describe_sends, recipients
from .notify import notify
from .periods import KINDS, period_end, report_title


def run(kind, end=None, send=True, start=None, to=None):
    """Build a report in both styles; email the full one to `to`, or each style to its
    recipients from Settings when send=True."""
    pdfs, subject, lines, errors = build(kind, end, start)
    what = report_title(kind, pdfs["full"])
    sends = [("full", to)] if to else (recipients(kind) if send else [])
    results = deliver(pdfs, subject, lines, sends)
    failed = [(style, t, err) for style, t, err in results if err]
    sent = describe_sends(results)
    notify(f"{what} was made from the command line" + (f" and {sent}" if sent else ""), file=pdfs["full"].name)
    for style, t, err in failed:
        notify(
            f"Emailing the {style} {what[0].lower() + what[1:]} to {t} failed: {err}",
            "error",
            file=pdfs["full"].name,
            admins_only=True,
        )
    if failed:
        raise RuntimeError(f"{len(failed)} email(s) failed")
    return pdfs["full"]


def main():
    load_settings()  # applies the configured time zone to command-line runs too
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("kind", choices=list(KINDS))
    r.add_argument("--end", help="last day to include, e.g. 2026-11-01; default: the last closed period")
    r.add_argument("--start", help="first day to include (custom reports only), e.g. 2026-11-01")
    r.add_argument("--no-email", action="store_true")
    a = ap.parse_args()

    def day(x):
        return dt.datetime.combine(dt.date.fromisoformat(x), dt.time(), config.TZ)

    if a.kind == "custom":
        if not a.start:
            ap.error("custom reports need --start (and optionally --end, default today)")
        end = day(a.end) + dt.timedelta(days=1) if a.end else dt.datetime.now(config.TZ)
        run("custom", end, send=False, start=day(a.start))
    else:
        # --end names the last day of the period; internally the end is exclusive
        end = day(a.end) + dt.timedelta(days=1) if a.end else None
        if end and end != period_end(a.kind, end):
            ap.error(f"--end for a {a.kind} report must be the last day of a {KINDS[a.kind]}")
        run(a.kind, end, send=not a.no_email)
