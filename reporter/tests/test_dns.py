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
        "/api/stats/database/top_clients": {"clients": [{"ip": "127.0.0.1", "name": "localhost", "count": 100},
                                                        {"ip": "10.0.0.7", "name": None, "count": 700},
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
        if path == "/api/queries":  # the server's own queries: 100 lookups of google.com, all from cache
            if params.get("client_ip") != "127.0.0.1":
                return {"recordsFiltered": 0, "queries": []}
            return {"recordsFiltered": 100, "queries": [{"domain": "google.com", "status": "CACHE"}] * 4}
        if path == "/api/stats/database/top_domains":
            if (params or {}).get("blocked"):
                return {"domains": [{"domain": "ads.example", "count": 90}]}
            return {"domains": [{"domain": "google.com", "count": 120}, {"domain": "example.com", "count": 90}]}
        return answers.get(path, {})

    monkeypatch.setattr(dns._Http, "call", call)


def test_pihole_figures_and_logout(monkeypatch):
    calls = []
    fake_pihole(monkeypatch, calls)
    cur, prev = dns.read({"kind": "pihole", "url": "http://ph", "secret": "pw"}, START, END, START - (END - START))
    # the server's own 100 cached lookups of google.com are taken out of every figure
    assert cur["error"] is None and cur["own"] == 100 and cur["total"] == 900 and cur["blocked"] == 150
    assert cur["cached"] == 200 and cur["forwarded"] == 550 and cur["clients"] == 11 and cur["version"] == "Pi-hole 6.1"
    assert [(r["name"], r["n"]) for r in cur["top_domains"]] == [("example.com", 90), ("google.com", 20)]
    assert cur["top_blocked"][0]["name"] == "ads.example"
    assert "127.0.0.1" not in [c["ip"] for c in cur["top_clients"]]
    assert sum(x[1] for x in cur["series"]) == 900
    assert [u["name"] for u in cur["upstreams"]] == ["one.one.one.one"]  # the blocklist pseudo-upstream is left out
    assert calls[0][1] == "/api/auth" and calls[0][2] is None
    assert calls[1][3]["X-FTL-SID"] == "abc"
    assert calls[-1][:2] == ("DELETE", "/api/auth")  # the session is given back
    assert prev["total"] == 900 and prev["top_domains"] == []


def test_a_server_that_fails_does_not_break_the_report(monkeypatch):
    def boom(self, *a, **k):
        raise TimeoutError

    monkeypatch.setattr(dns._Http, "call", boom)
    cur, prev = dns.read({"kind": "technitium", "url": "http://t", "secret": "x"}, START, END, START)
    assert cur["error"] == "it didn't answer in time" and cur["total"] is None


def test_technitium_reads_exact_hours_in_chunks(monkeypatch):
    asked, auth = [], set()

    def call(self, path, params=None, method="GET", body=None):
        auth.add(self.headers.get("Authorization"))
        p = params or {}
        if path == "/api/user/session/get":
            return {"status": "ok", "info": {"version": "15.6.2"}}
        if path == "/api/settings/get":
            return {"status": "ok", "response": {"blockingType": "NxDomain"}}
        start = dns._parse_label(p["start"])
        if path == "/api/dashboard/stats/getTop":
            key = {"TopClients": "topClients", "TopDomains": "topDomains", "TopBlockedDomains": "topBlockedDomains"}
            item = {"TopClients": "10.0.0.7", "TopDomains": "example.com", "TopBlockedDomains": "ads.example"}
            extra = [{"name": "10.0.0.8", "hits": 1}] if p["statsType"] == "TopClients" and start.day == 2 else []
            return {"status": "ok", "response": {key[p["statsType"]]: [{"name": item[p["statsType"]], "hits": 10}] + extra}}
        asked.append((p["start"], p["end"]))
        hours = round((dns._parse_label(p["end"]) - start).total_seconds() / 3600) + 1
        labels = [(start + dt.timedelta(hours=h + 1)).strftime("%Y-%m-%dT%H:%M:%S.0000000Z") for h in range(hours)]
        return {"status": "ok", "response": {
            "stats": {"totalQueries": 100 * hours, "totalBlocked": 10 * hours, "totalCached": 50 * hours,
                      "totalRecursive": 40 * hours, "totalServerFailure": hours, "totalNxDomain": 12 * hours,
                      "blockListZones": 5000},
            "mainChartData": {"labels": labels, "datasets": [{"label": "Total", "data": [100] * hours},
                                                             {"label": "Blocked", "data": [10] * hours}]}}}

    monkeypatch.setattr(dns._Http, "call", call)
    cur, prev = dns.read({"kind": "technitium", "url": "http://t:5380", "secret": "tok"}, START, END, START - (END - START))
    assert auth == {"Bearer tok"}
    # a week is a 6-day and a 1-day request, each ending an hour early because Technitium includes the end hour
    assert asked[:2] == [("2026-11-02T00:00:00.000Z", "2026-11-07T23:00:00.000Z"),
                         ("2026-11-08T00:00:00.000Z", "2026-11-08T23:00:00.000Z")]
    assert cur["total"] == 16800 and cur["blocked"] == 1680 and cur["failed"] == 168 and prev["total"] == 16800
    assert cur["nxdomain"] == 336  # blocked answers taken out of the NXDOMAIN count
    assert cur["series"][0][0] == START.timestamp()  # labels mark the end of each hour
    assert cur["top_clients"][0] == {"name": "10.0.0.7", "ip": "10.0.0.7", "n": 20.0} and cur["clients"] == 2
    assert cur["version"] == "Technitium 15.6.2"
    rows, step = dns.buckets(cur["series"], START, END)
    assert step == 86400 and [r[1] for r in rows] == [2400] * 7


def test_devices_are_named_only_when_certain():
    names = {"10.0.0.7": ("Kitchen iPad", "aa:01"), "10.0.0.8": ("Kitchen iPad", "aa:01"), "10.0.0.9": None}
    rows = dns.name_clients([
        {"name": "10.0.0.9", "ip": "10.0.0.9", "n": 50},  # two devices had this address: no name
        {"name": "10.0.0.7", "ip": "10.0.0.7", "n": 30},
        {"name": "nas.lan", "ip": "10.0.0.20", "n": 25},  # UniFi never saw it: the DNS server's name stays
        {"name": "10.0.0.8", "ip": "10.0.0.8", "n": 40},  # the iPad's second address: one row, counts added
        {"name": "localhost", "ip": "127.0.0.1", "n": 5},
    ], names)
    assert [(r["name"], r["n"], r["detail"]) for r in rows] == [
        ("Kitchen iPad", 70, "10.0.0.7, 10.0.0.8"),
        ("More than one device", 50, "10.0.0.9"),
        ("nas.lan", 25, "10.0.0.20"),
        ("The DNS server itself", 5, "127.0.0.1"),
    ]


def test_unifi_names_by_address():
    from app.collect import client_names

    class FakeSource:
        def prom(self, q):
            def m(ip, mac, name=None, oui=None):
                return {"metric": {k: v for k, v in (("ip", ip), ("mac", mac), ("name", name), ("oui", oui)) if v}}
            return [m("10.0.0.7", "aa:01", "Kitchen iPad"), m("10.0.0.8", "aa:01", "Kitchen iPad"),
                    m("10.0.0.9", "bb:02", "Pixel"), m("10.0.0.9", "cc:03", "Mac"),
                    m("10.0.0.10", "dd:04:00:00:00:01", oui="Dell Inc.")]

    assert client_names(FakeSource()) == {"10.0.0.7": ("Kitchen iPad", "aa:01"), "10.0.0.8": ("Kitchen iPad", "aa:01"),
                                          "10.0.0.9": None, "10.0.0.10": ("Dell Inc. device", "dd:04:00:00:00:01")}


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
            "dns_enabled": "on", "dns_rows": "1", "dns_url_0": "http://10.0.0.53/", "dns_kind_0": "pihole",
            "dns_secret_0": "app-pw"}
    assert c.post("/settings", data=form).status_code == 302
    assert config.load_settings()["dns"]["servers"] == [
        {"kind": "pihole", "name": "Pi-hole", "url": "http://10.0.0.53", "secret": "app-pw", "verify": True}]
    page = c.get("/settings").get_data(as_text=True)
    assert 'name="sec_dns"' in page and "app-pw" not in page
    form.update(dns_rows="2", dns_secret_0="", dns_name_0="Office Pi-hole")
    c.post("/settings", data=form)
    (srv,) = config.load_settings()["dns"]["servers"]
    assert srv["secret"] == "app-pw" and srv["name"] == "Office Pi-hole"
    # the switch turns the whole section off but keeps the servers
    off = {k: v for k, v in form.items() if k != "dns_enabled"}
    c.post("/settings", data=off)
    s = config.load_settings()
    assert s["dns"]["enabled"] is False and len(s["dns"]["servers"]) == 1 and dns.servers(s) == []
    page = c.get("/settings").get_data(as_text=True)
    assert 'name="sec_dns"' not in page and 'id="dns-body" hidden' in page
    # a removed row is simply missing from the form
    c.post("/settings", data={k: v for k, v in form.items() if not k.startswith("dns_") or k in ("dns_enabled", "dns_rows")})
    assert config.load_settings()["dns"]["servers"] == []


def test_dns_test_button_checks_without_saving(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS", tmp_path / "settings.json")
    s = config.load_settings()
    s["dns"]["servers"] = [{"kind": "technitium", "name": "T", "url": "https://10.0.0.5:53443", "secret": "tok", "verify": False}]
    config.save_settings(s)
    seen = []
    monkeypatch.setattr(dns, "test", lambda srv: seen.append(srv) or (None if srv["secret"] == "tok" else "the API token was refused"))
    c = web.app.test_client()
    with c.session_transaction() as sess:
        sess.update(user="Juby", login="juby", admin=True, csrf="t")
    form = {"csrf": "t", "i": "0", "url": "https://10.0.0.5:53443", "kind": "technitium", "secret": "", "noverify": "1"}
    assert c.post("/settings/dns-test", data=form).get_json() == {"ok": True, "message": "Technitium answered"}
    assert seen[-1] == {"kind": "technitium", "url": "https://10.0.0.5:53443", "secret": "tok", "verify": False}
    r = c.post("/settings/dns-test", data={**form, "url": "https://10.0.0.6"}).get_json()  # new address: no saved token
    assert r == {"ok": False, "message": "The API token was refused"}
    assert c.post("/settings/dns-test", data={**form, "csrf": "x"}).status_code == 400
    assert config.load_settings()["dns"]["servers"][0]["url"] == "https://10.0.0.5:53443"  # nothing saved


def test_certificate_option_applies_after_a_redirect_to_https():
    assert dns._Http("http://10.0.0.5:5380", verify=False).ctx.verify_mode.name == "CERT_NONE"
    assert dns._Http("http://10.0.0.5:5380", verify=True).ctx is None


def test_technitium_own_checks_come_off_the_matching_domain():
    out = server(total=1000, cached=900, recursive=100, blocked=10, clients=5,
                 top_clients=[{"name": "127.0.0.1", "ip": "127.0.0.1", "n": 700}, {"name": "x", "ip": "10.0.0.7", "n": 300}],
                 top_domains=[{"name": "google.com", "n": 705}, {"name": "example.com", "n": 50}],
                 series=[(0, 500, 5), (3600, 500, 5)])
    out["_own"] = {"n": 700, "cached": 700, "recursive": 0, "blocked": 0, "domains": None}
    dns.drop_own(out)
    assert (out["total"], out["cached"], out["clients"], out["own"]) == (300, 200, 4, 700)
    assert [(r["name"], r["n"]) for r in out["top_domains"]] == [("example.com", 50), ("google.com", 5)]
    assert [c["ip"] for c in out["top_clients"]] == ["10.0.0.7"] and [x[1] for x in out["series"]] == [150, 150]
