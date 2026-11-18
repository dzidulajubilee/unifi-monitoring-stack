"""Regression tests for the pre-release review: sign-in redirects and rate limit, the report renderer's file
access, and the report worker surviving errors."""

import base64
import datetime as dt
import time

import pytest
from app import build, config, web

PNG = base64.b64decode(  # 1x1 pixel
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def client(monkeypatch):
    web.app.config.update(TESTING=True)
    web._fails.clear()
    monkeypatch.setattr(web, "grafana_login", lambda u, p: ("Ama", False, "ama") if p == "right" else None)
    return web.app.test_client()


@pytest.mark.parametrize("nxt", ["/settings", "/download/weekly-2026-01-05.pdf?view=1"])
def test_sign_in_returns_to_local_pages(client, nxt):
    r = client.post(f"/login?next={nxt}", data={"username": "ama", "password": "right"})
    assert r.headers["Location"] == nxt


@pytest.mark.parametrize("nxt", ["//example.com", "/\\example.com", "/\\/example.com", "https://example.com", "/\t/x"])
def test_sign_in_never_redirects_to_another_site(client, nxt):
    r = client.post("/login", query_string={"next": nxt}, data={"username": "ama", "password": "right"})
    assert r.headers["Location"] == "/"


def test_sign_in_limit_is_per_client_not_per_proxy(client):
    def attempt(ip, password="wrong"):
        r = client.post("/login", data={"username": "ama", "password": password},
                        headers={"X-Forwarded-For": ip}, base_url="https://localhost")
        return r.get_data(as_text=True)

    for _ in range(5):
        attempt("192.0.2.10")
    assert "Too many failed attempts" in attempt("192.0.2.10")
    # nginx is the only direct client; someone else must still be able to sign in
    assert "Wrong username or password" in attempt("192.0.2.20")


def test_sign_in_limit_forgets_quiet_addresses(client):
    web._fails["192.0.2.30"] = [time.time() - 600]
    client.post("/login", data={"username": "ama", "password": "wrong"}, headers={"X-Forwarded-For": "192.0.2.40"})
    assert "192.0.2.30" not in web._fails


def test_session_cookie_is_https_only(client):
    r = client.post("/login", data={"username": "ama", "password": "right"}, base_url="https://localhost")
    assert "Secure" in r.headers["Set-Cookie"]


def test_report_renderer_loads_only_its_own_files(tmp_path):
    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(PNG)
    font = (config.ROOT / "fonts" / "IBMPlexSans-400.ttf").as_uri()
    assert build.allowed_file(font) and build.allowed_file("data:image/png;base64,AA==")
    for url in (outside.as_uri(), "file:///etc/passwd", "http://192.0.2.1/x.png",
                (config.ROOT / "fonts" / ".." / "app" / "web.py").as_uri()):
        assert not build.allowed_file(url)
        with pytest.raises(ValueError):
            build.local_files_only().fetch(url)

    def images(src):
        pdf = tmp_path / "t.pdf"
        build.render_pdf(f"<html><head></head><body><img src='{src}' style='width:20px'></body></html>", pdf)
        return pdf.read_bytes().count(b"/Image")

    data = "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert images(data) > 0  # the check itself sees images
    assert images(outside.as_uri()) == 0


def test_worker_keeps_going_after_an_error(monkeypatch, tmp_path):
    made = tmp_path / "weekly-2026-01-05.pdf"
    made.write_bytes(b"%PDF")
    monkeypatch.setattr(web.R, "build", lambda *a: ({"full": made, "summary": made}, "s", [], 0, {}))
    monkeypatch.setattr(web.R, "load_settings", lambda: {"smtp": {}})
    calls = []

    def record_made(*a):
        calls.append(a)
        if len(calls) == 1:
            raise OSError("disk full")

    monkeypatch.setattr(web.R, "record_made", record_made)
    end = dt.datetime(2026, 1, 12, tzinfo=config.TZ)
    first = web.enqueue("weekly", end - dt.timedelta(days=7), end, "ama")
    second = web.enqueue("weekly", end - dt.timedelta(days=7), end, "ama")
    web.threading.Thread(target=web.worker, daemon=True).start()
    deadline = time.time() + 10
    while web.jobs[second]["status"] in ("queued", "running") and time.time() < deadline:
        time.sleep(0.05)
    assert len(calls) == 2 and web.jobs[first]["status"] == "done"  # the PDF was made before the error
    assert web.jobs[second]["status"] == "done"
