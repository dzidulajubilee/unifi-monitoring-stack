import datetime as dt

from app.collect import _sum_by, _total
from app.sources import FW_IN, FW_SYSLOG_IN, Source

START = dt.datetime(2026, 11, 9, tzinfo=dt.timezone.utc)
END = START + dt.timedelta(days=7)


def source(syslog_start, calls):
    s = Source(START, END)
    s._syslog_start = syslog_start

    def loki(q):
        calls.append(("loki", q))
        return [{"metric": {"src": "a"}, "value": [0, "5"]}]

    def loki_at(q, t_from, t_to):
        calls.append(("at", q, t_from, t_to))
        return [{"metric": {"src": "a" if FW_IN in q else "b"}, "value": [0, "3" if FW_IN in q else "2"]}]

    s.loki, s.loki_at = loki, loki_at
    return s


def metric(sel):
    return f"sum by (src) (count_over_time({sel} [$R]))"


def test_without_syslog_the_system_log_is_used():
    calls = []
    rows = source(None, calls).internet(metric)
    assert [c[0] for c in calls] == ["loki"] and FW_IN in calls[0][1]
    assert _total(rows) == 5


def test_syslog_from_mid_period_splits_at_its_start():
    calls = []
    t0 = (START + dt.timedelta(days=5)).timestamp()
    rows = source(t0, calls).internet(metric)
    syslog, system_log = calls
    assert FW_SYSLOG_IN in syslog[1] and syslog[2:] == (t0 - 1, END.timestamp())
    assert FW_IN in system_log[1] and system_log[2:] == (START.timestamp(), t0 - 1)
    assert _sum_by(rows, "src") == {"b": 2, "a": 3}


def test_syslog_for_the_whole_period_reads_only_syslog():
    calls = []
    source(START.timestamp() + 30, calls).internet(metric)
    assert len(calls) == 1 and FW_SYSLOG_IN in calls[0][1]


def test_a_failed_part_gives_none():
    s = source(START.timestamp() + 86400, [])
    s.loki_at = lambda *a: None
    assert s.internet(metric) is None and _total(None) is None and _sum_by(None, "src") is None
