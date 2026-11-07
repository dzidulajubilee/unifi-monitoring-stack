import pytest
from app import notify, web


@pytest.fixture
def client():
    web.app.config.update(TESTING=True)
    return web.app.test_client()


def signed_in(client, admin=True, login="juby"):
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
