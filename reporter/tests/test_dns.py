import datetime as dt

from app import config, dns, web
from app.text import attention, summary

UTC = dt.timezone.utc
START = dt.datetime(2026, 11, 2, tzinfo=UTC)
END = START + dt.timedelta(days=7)


def fake_pihole(monkeypatch, calls=None):
    t0 = START.timestamp()
    answers = {
        "/api/auth": {"session": {"valid": True, "sid": "abc", "validity": 1800}},
        "/api/stats/database/summary": {"sum_queries": 1000, "sum_blocked": 150, "percent_blocked": 15.0,
                                        "total_clients": 12},
        "/api/history/database": {"history": [
            {"timestamp": t0 + 600, "total": 600, "cached": 200, "blocked": 100, "forwarded": 300},
            {"timestamp": t0 + 86400 + 600, "total": 400, "cached": 100, "blocked": 50, "forwarded": 250},
        ]},
        "/api/stats/database/top_clients": {"clients": [{"ip": "10.0.0.7", "name": None, "count": 700},
                                                        {"ip": "10.0.0.9", "name": "nas.lan", "count": 300}]},
        "/api/stats/database/upstreams": {"upstreams": [
            {"ip": "blocklist", "name": "blocklist", "port": -1, "count": 150},
            {"ip": "1.1.1.1", "name": "one.one.one.one", "port": 53, "count": 550}]},
        "/api/stats/summary": {"gravity": {"domains_being_blocked": 120000, "last_update": int(t0)}},
        "/api/info/version": {"version": {"core": {"local": {"version": "v6.1"}}}},
    }

    def call(self, path, params=None, method="GET", body=None):
        if calls is not None:
            calls.append((method, path, params, dict(self.headers)))
        if path == "/api/stats/database/top_domains":
            name = "ads.example" if (params or {}).get("blocked") else "example.com"
            return {"domains": [{"domain": name, "count": 90}]}
        return answers.get(path, {})

    monkeypatch.setattr(dns._Http, "call", call)


def test_pihole_figures_and_logout(monkeypatch):
    calls = []
    fake_pihole(monkeypatch, calls)
    cur, prev = dns.read({"kind": "pihole", "url": "http://ph", "secret": "pw"}, START, END, START - (END - START))
    assert cur["error"] is None and cur["total"] == 1000 and cur["blocked"] == 150 and cur["cached"] == 300
    assert cur["forwarded"] == 550 and cur["clients"] == 12 and cur["version"] == "Pi-hole 6.1"
    assert cur["top_blocked"][0]["name"] == "ads.example" and cur["top_domains"][0]["name"] == "example.com"
    assert [u["name"] for u in cur["upstreams"]] == ["one.one.one.one"]  # the blocklist pseudo-upstream is left out
    assert calls[0][1] == "/api/auth" and calls[0][2] is None
    assert calls[1][3]["X-FTL-SID"] == "abc"
    assert calls[-1][:2] == ("DELETE", "/api/auth")  # the session is given back
    assert prev["total"] == 1000 and prev["top_domains"] == []


def test_a_server_that_fails_does_not_break_the_report(monkeypatch):
    def boom(self, *a, **k):
        raise TimeoutError

    monkeypatch.setattr(dns._Http, "call", boom)
    cur, prev = dns.read({"kind": "technitium", "url": "http://t", "secret": "x"}, START, END, START)
    assert cur["error"] == "it didn't answer in time" and cur["total"] is None


def test_technitium_figures(monkeypatch):
    seen = {}

    def call(self, path, params=None, method="GET", body=None):
        seen.setdefault("auth", self.headers.get("Authorization"))
        if path == "/api/user/session/get":
            return {"status": "ok", "info": {"version": "13.6"}}
        seen.setdefault("params", params)
        return {"status": "ok", "response": {
            "stats": {"totalQueries": 2000, "totalBlocked": 100, "totalCached": 900, "totalRecursive": 800,
                      "totalServerFailure": 60, "totalNxDomain": 40, "totalClients": 9, "blockListZones": 5000},
            "mainChartData": {"labels": ["2026-11-02T00:00:00.0000000Z", "2026-11-03T00:00:00Z"],
                              "datasets": [{"label": "Total", "data": [1500, 500]},
                                           {"label": "Blocked", "data": [80, 20]}]},
            "topClients": [{"name": "10.0.0.7", "domain": "", "hits": 1200}],
            "topDomains": [{"name": "example.com", "hits": 300}],
            "topBlockedDomains": [{"name": "ads.example", "hits": 70}],
        }}

    monkeypatch.setattr(dns._Http, "call", call)
    cur, _ = dns.read({"kind": "technitium", "url": "http://t:5380", "secret": "tok"}, START, END, START - (END - START))
    assert seen["auth"] == "Bearer tok"
    assert seen["params"]["type"] == "Custom" and seen["params"]["start"] == "2026-11-02T00:00:00.000Z"
    assert cur["failed"] == 60 and cur["version"] == "Technitium 13.6"
    rows, step = dns.buckets(cur["series"], START, END)
    assert step == 86400 and rows[0][1:] == (1500, 80) and rows[1][1:] == (500, 20) and rows[2][1] is None


def test_devices_get_unifi_names():
    rows = dns.name_clients([{"name": "10.0.0.7", "ip": "10.0.0.7", "n": 5}, {"name": "nas.lan", "ip": "10.0.0.9", "n": 1}],
                            {"10.0.0.7": "Kitchen iPad"})
    assert rows[0]["name"] == "Kitchen iPad" and rows[0]["detail"] == "10.0.0.7"
    assert rows[1]["name"] == "nas.lan" and rows[1]["detail"] == "10.0.0.9"


def server(**kw):
    s = dns._blank({"kind": "technitium", "name": "Technitium"})
    s.update(kw)
    return s


BASE = {"aps": [], "gw_reboots": 0, "mem_end": None, "mem_start": None, "mem_max": None, "threats_open": 0,
        "threats_high": 0, "logins_outside": 0, "threat_devices": [], "_word": "week"}


def test_dns_attention_and_summary():
    cur = server(total=1000, blocked=300, failed=50, clients=4, top_clients=[{"name": "Kitchen iPad", "n": 600}])
    before = server(total=1000, blocked=100)
    d = {**BASE, "dns": [cur, server(name="Pi-hole", error="the password was refused")], "_dns_prev": [before, None]}
    items = attention(d, {"dns"})
    assert items[0] == ("Technitium blocked 30% of queries, up from 10% last week; Kitchen iPad asked the most. "
                        "Check the top blocked domains for a device that keeps calling a blocked service")
    assert items[1].startswith("5.0% of lookups on Technitium failed")
    assert items[2] == "Pi-hole couldn't be read: the password was refused. Its DNS figures are missing"
    assert attention(d, set()) == []
    d.update(wans=[], wan_down=None, clients_peak=None, blocks_in=None)
    lines = summary(d, {"dns": [before, None]}, "week", {"prom": True, "loki": True}, {"dns"})
    assert lines == ["Technitium answered 1,000 DNS queries from 4 devices and blocked 30% of them (+20 points vs last week)."]


def test_settings_keep_the_saved_token(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS", tmp_path / "settings.json")
    c = web.app.test_client()
    with c.session_transaction() as s:
        s.update(user="Juby", login="juby", admin=True, csrf="t")
    page = c.get("/settings").get_data(as_text=True)
    assert "DNS servers" in page and 'name="sec_dns"' not in page  # no tick until a server is added
    form = {"csrf": "t", "hour": "7", "port": "587", "timezone": "UTC", "sec_summary": "on", "sum_summary": "on",
            "dns_rows": "1", "dns_url_0": "http://10.0.0.53/", "dns_kind_0": "pihole", "dns_secret_0": "app-pw"}
    assert c.post("/settings", data=form).status_code == 302
    assert config.load_settings()["dns"]["servers"] == [
        {"kind": "pihole", "name": "Pi-hole", "url": "http://10.0.0.53", "secret": "app-pw", "verify": True}]
    page = c.get("/settings").get_data(as_text=True)
    assert 'name="sec_dns"' in page and "app-pw" not in page
    form.update(dns_rows="2", dns_secret_0="", dns_name_0="Office Pi-hole")
    c.post("/settings", data=form)
    (srv,) = config.load_settings()["dns"]["servers"]
    assert srv["secret"] == "app-pw" and srv["name"] == "Office Pi-hole"
    form.update(dns_remove_0="on")
    c.post("/settings", data=form)
    assert config.load_settings()["dns"]["servers"] == []
