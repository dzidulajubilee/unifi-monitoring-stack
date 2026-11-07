import datetime as dt
import json

from app import config
from app.links import grafana_linker


def test_old_settings_files_get_new_defaults(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"general": {"name": "Acme"}, "sections": {"apps": False}}))
    monkeypatch.setattr(config, "SETTINGS", f)
    s = config.load_settings()
    assert s["general"]["name"] == "Acme" and s["sections"]["apps"] is False and s["sections"]["wifi"] is True
    assert {k for k, v in s["sections_summary"].items() if v} == config.SUMMARY_SECTIONS
    assert s["recipients_summary"]["weekly"] == ""


def test_time_zone_setting_applies_everywhere(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"general": {"timezone": "Europe/London"}}))
    monkeypatch.setattr(config, "SETTINGS", f)
    config.load_settings()
    assert str(config.TZ) == "Europe/London"
    f.write_text(json.dumps({"general": {"timezone": "Mars/Olympus"}}))
    config.load_settings()
    assert str(config.TZ) == "Europe/London"  # an unknown zone keeps the current one
    config.set_timezone("Africa/Accra")


def test_grafana_links():
    s = {"general": {"grafana_url": "http://10.0.0.5:3000/", "grafana_links": True}}
    start = dt.datetime(2026, 10, 26, tzinfo=dt.timezone.utc)
    link = grafana_linker(s, start, start + dt.timedelta(days=7))
    assert link("clients", Client="iPhone 12") == (
        "http://10.0.0.5:3000/d/jMfvAjxWz?orgId=1&from=1792972800000&to=1793577600000&var-Client=iPhone%2012")
    assert grafana_linker({"general": {"grafana_url": "", "grafana_links": True}}, start, start) is None
    assert grafana_linker({"general": {"grafana_url": "http://x", "grafana_links": False}}, start, start) is None
