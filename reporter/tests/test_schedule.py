import datetime as dt

import pytest
from app import config, history, web
from app.periods import report_path
from app.schedule import due_reports

TZ = config.TZ
MONDAY_8AM = dt.datetime(2026, 11, 9, 8, tzinfo=TZ)  # last week (2 to 8 Nov) closed, past the 07:00 send hour
LAST_WEEK = dt.datetime(2026, 11, 2, tzinfo=TZ)


@pytest.fixture
def fresh():
    s = config.load_settings()
    s["schedule"].update(enabled=True, hour=7, kinds=["weekly"])
    config.save_settings(s)
    (config.OUT / ".scheduler-since").write_text(dt.datetime(2026, 10, 1, tzinfo=TZ).isoformat())
    pdf = report_path("weekly", LAST_WEEK)
    for f in (history.SENT, history.MADE, pdf):
        f.unlink(missing_ok=True)
    yield pdf
    for f in (history.SENT, history.MADE, pdf):
        f.unlink(missing_ok=True)


def test_a_report_made_by_hand_does_not_stop_the_scheduled_email(fresh):
    assert due_reports(MONDAY_8AM) == [("weekly", MONDAY_8AM.replace(hour=0))]
    fresh.write_bytes(b"%PDF made by hand at 06:30")
    assert due_reports(MONDAY_8AM) == [("weekly", MONDAY_8AM.replace(hour=0))]


def test_once_sent_deleting_the_report_does_not_send_it_again(fresh):
    history.mark_sent("weekly", MONDAY_8AM.replace(hour=0))
    assert due_reports(MONDAY_8AM) == []
    assert not fresh.exists()


def test_after_an_upgrade_reports_already_on_disk_count_as_sent(fresh):
    fresh.write_bytes(b"%PDF sent before the upgrade")
    assert due_reports(MONDAY_8AM) == []
    assert history.sent_periods() == {"weekly": "2026-11-09"}


def test_the_list_says_who_remade_a_report_and_delete_forgets_it(fresh):
    fresh.write_bytes(b"%PDF")
    history.record_made(fresh.name, "Ama", remade=True)
    c = web.app.test_client()
    with c.session_transaction() as s:
        s.update(user="Admin", login="admin", admin=True, csrf="t")
    assert "remade by Ama" in c.get("/").get_data(as_text=True)
    c.post(f"/delete/{fresh.name}", data={"csrf": "t"})
    assert fresh.name not in history.made()


def test_remade_note_wording():
    assert web.remade_note(None) is None
    assert web.remade_note({"by": "Ama", "remade": False}) is None
    assert web.remade_note({"by": "schedule", "remade": True}) == "remade automatically"
    assert web.remade_note({"by": "cli", "remade": True}) == "remade from the command line"
