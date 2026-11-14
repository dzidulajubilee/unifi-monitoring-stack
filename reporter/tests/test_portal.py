import pytest
from app import notify, web


@pytest.fixture
def client():
    web.app.config.update(TESTING=True)
    return web.app.test_client()


def signed_in(client, admin=True, login="admin"):
    with client.session_transaction() as s:
        s.update(user=login.title(), login=login, admin=admin, csrf="t")
    return client


def test_pages_need_sign_in(client):
    r = client.get("/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_settings_are_for_admins_only(client):
    assert signed_in(client, admin=False).get("/settings").status_code == 403
    assert signed_in(client, admin=True).get("/settings").status_code == 200


def test_posts_need_the_csrf_token(client):
    assert signed_in(client).post("/generate", data={"kind": "weekly"}).status_code == 400


def test_downloads_only_serve_report_files(client):
    assert signed_in(client).get("/download/..%2fsettings.json").status_code == 404


def test_notification_visibility_and_read_state(client):
    notify.notify("public note")
    notify.notify("mail failed", "error", admins_only=True, for_user="kofi")
    ama = signed_in(web.app.test_client(), admin=False, login="ama").get("/notifications").get_json()
    assert [n["text"] for n in ama["items"]][:1] == ["public note"]
    kofi = signed_in(web.app.test_client(), admin=False, login="kofi")
    assert kofi.get("/notifications").get_json()["items"][0]["text"] == "mail failed"
    assert kofi.post("/notifications/read", data={"csrf": "t", "all": "1"}).get_json()["unread"] == 0


def test_clear_all_is_per_user():
    notify.notify("to be cleared")
    admin = signed_in(web.app.test_client(), admin=True, login="admin")
    ama = signed_in(web.app.test_client(), admin=False, login="ama")
    assert admin.post("/notifications/clear", data={"all": "1"}).status_code == 400  # needs the csrf token
    after = admin.post("/notifications/clear", data={"csrf": "t"}).get_json()
    assert after == {"unread": 0, "items": []}
    assert "to be cleared" in [n["text"] for n in ama.get("/notifications").get_json()["items"]]
    notify.notify("arrived later")
    assert [n["text"] for n in admin.get("/notifications").get_json()["items"]] == ["arrived later"]


def test_only_admins_can_email_a_report(client, monkeypatch):
    sent = []
    form = {"kind": "weekly", "email": "on", "to": "someone@example.com", "csrf": "t"}
    with monkeypatch.context() as m:
        m.setattr(web, "enqueue", lambda *a, **k: sent.append(a[4]))
        m.setattr(web.R, "load_settings", lambda: {"smtp": {"host": "mail.example.com"}})
        signed_in(client, admin=False, login="viewer").post("/generate", data=form)
        signed_in(client, admin=True).post("/generate", data=form)
    assert sent == [[], [("full", "someone@example.com")]]
    assert 'name="email"' not in signed_in(client, admin=False, login="viewer").get("/").get_data(as_text=True)


def test_mail_checks_the_server_certificate(monkeypatch):
    from app import mail

    seen = {}

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self, context=None): seen["ctx"] = context
        def send_message(self, m): pass

    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    mail.send_mail("a@example.com", "s", "t", smtp={"host": "h", "security": "starttls"})
    assert seen["ctx"].verify_mode == mail.ssl.CERT_REQUIRED and seen["ctx"].check_hostname
    mail.send_mail("a@example.com", "s", "t", smtp={"host": "h", "security": "starttls", "verify": False})
    assert seen["ctx"].verify_mode == mail.ssl.CERT_NONE
