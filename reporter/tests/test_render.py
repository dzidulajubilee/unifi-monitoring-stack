"""Renders the real report (template, charts, PDF and the page-flow check) from made-up data in several shapes,
so a template change that breaks the layout fails here instead of in a report someone receives.
Nothing is fetched: only the database queries are replaced."""

import datetime as dt

import fake_report as F
import pytest
from app import build as B
from app import config


class FakeSource:
    def __init__(self, start, end):
        self.start, self.end = start, end
        self.errors = []

    def coverage(self):
        return {"prom": 1.0, "loki": 1.0}


def patch(monkeypatch, size):
    data = {}

    def collect(s):
        if not data:
            data["d"], data["p"] = F.collect(size, s.start, s.end)
            return dict(data["d"])
        return dict(data["p"])  # the earlier period

    st = config.load_settings()
    st["general"]["name"] = "Example Company with a fairly long name" if size == "huge" else "Example"
    st["sections"] = {k: True for k, *_ in config.SECTIONS}
    st["sections_summary"] = {k: True for k, *_ in config.SECTIONS}
    monkeypatch.setattr(B, "load_settings", lambda: st)
    monkeypatch.setattr(B, "Source", FakeSource)
    monkeypatch.setattr(B, "collect", collect)
    monkeypatch.setattr(B, "collect_wifi", lambda s: data["d"]["wifi"])
    monkeypatch.setattr(B, "collect_apps", lambda s, prev=None: data["d"]["dpi"])
    monkeypatch.setattr(B, "collect_isp", lambda s: data["d"]["isp"])
    monkeypatch.setattr(B, "collect_maint", lambda s: data["d"]["maint"])
    monkeypatch.setattr(B, "collect_trends", lambda kind, start: data["d"]["trends"])
    monkeypatch.setattr(B, "client_names", lambda s: {})
    monkeypatch.setattr(B, "events", lambda s, wan=True: [
        {"t": s.start + dt.timedelta(hours=6 * i), "kind": "Firewall change",
         "msg": f"Admin {i + 1} made 3 changes to 2 rules: Policy {i + 1}, Policy {i + 2}"}
        for i in range(12 * F.SIZES[size])
    ])
    monkeypatch.setattr(B, "threat_list", lambda s: ([
        {"t": s.start + dt.timedelta(hours=i), "src": f"Desktop {i + 1}", "dst": f"198.51.100.{i + 1}",
         "sev": "High", "blocked": True} for i in range(8 * F.SIZES[size])
    ], 8 * F.SIZES[size]))

    def wan_series(s):
        hours = int((s.end - s.start).total_seconds() // 3600)
        step = 3600 if hours <= 24 * 10 else 21600
        pts = [(s.start.timestamp() + i * step, 50e6 + (i % 24) * 4e6) for i in range(hours * 3600 // step)]
        return (pts, [(t, v / 4) for t, v in pts], step) if F.SIZES[size] else ([], [], step)

    monkeypatch.setattr(B, "wan_series", wan_series)
    servers = [{"name": "Resolver", "kind": "pihole", "url": "http://192.0.2.53"}] if F.SIZES[size] else []
    monkeypatch.setattr(B.dns, "servers", lambda st: servers)
    monkeypatch.setattr(B.dns, "read", lambda srv, start, end, prev_start: F.dns_pair(size, start))
    monkeypatch.setattr(B, "logo_path", lambda: None)


PERIODS = {
    "weekly": (dt.date(2026, 11, 2), dt.date(2026, 11, 9)),
    "monthly": (dt.date(2026, 10, 1), dt.date(2026, 11, 1)),
    "quarterly": (dt.date(2026, 7, 1), dt.date(2026, 10, 1)),
}


@pytest.mark.parametrize("size", ["empty", "typical", "huge"])
@pytest.mark.parametrize("kind", ["weekly", "monthly", "quarterly"])
def test_report_renders_without_layout_problems(monkeypatch, tmp_path, size, kind):
    patch(monkeypatch, size)
    # its own folder, so these PDFs don't count as reports already made in other tests
    real = B.report_path
    monkeypatch.setattr(B, "report_path", lambda *a: tmp_path / real(*a).name)
    start, end = (dt.datetime.combine(x, dt.time(), config.TZ) for x in PERIODS[kind])
    pdfs, subject, lines, errors, pages = B.build(kind, end, start)
    assert pages == {"full": [], "summary": []}, pages
    for pdf in pdfs.values():
        assert pdf.exists() and pdf.stat().st_size > 10_000
