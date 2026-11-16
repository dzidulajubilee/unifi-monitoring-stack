from app.text import attention

BASE = {"aps": [], "gw_reboots": 0, "mem_end": None, "mem_start": None, "mem_max": None, "threats_open": 0,
        "threats_high": 0, "logins_outside": 0, "threat_devices": []}


def ap(name, retries=0.0, signal=None):
    return {"name": name, "reboots": 0, "u24": 0.2, "retries": retries, "signal": signal}


def test_access_point_problems_are_grouped_worst_first():
    d = {**BASE, "aps": [ap("Lobby", 0.28), ap("Warehouse", 0.89, -81), ap("Hall", 0.01)]}
    items = attention(d, {"aps"})
    assert items[0].startswith("Access points resending many Wi-Fi packets") and "Warehouse 89% and Lobby 28%" in items[0]
    assert "Hall" not in items[0]
    assert items[1] == ("Devices averaged a weak signal on Warehouse (-81 dBm); they're too far from the access point "
                        "or behind walls")


def test_access_point_restarts_are_one_line():
    d = {**BASE, "aps": [{**ap("Hall"), "reboots": 5}, {**ap("Office"), "reboots": 2}, {**ap("Warehouse"), "reboots": 6}]}
    assert attention(d, {"aps"})[0] == "3 access points restarted: Warehouse 6 times, Hall 5 times and Office twice"
    d["aps"] = d["aps"][:1]
    assert attention(d, {"aps"})[0] == "Access point Hall restarted 5 times"


def test_sections_switched_off_raise_nothing():
    d = {**BASE, "aps": [ap("Warehouse", 0.89)], "gw_reboots": 2}
    assert attention(d, set()) == []


def test_logins_from_own_address_are_marked():
    d = {**BASE, "logins_outside": 3, "own_ips": {"203.0.113.10"},
         "login_ips": [{"name": "203.0.113.10", "n": 2}, {"name": "198.51.100.20", "n": 1}]}
    (line,) = attention(d, {"security"})
    assert "203.0.113.10 (2, your own internet address)" in line and "198.51.100.20 (1)" in line


def test_peer_to_peer_names_the_device():
    d = {**BASE, "dpi": {"p2p": 0.13, "p2p_devices": [{"name": "Unnamed device (…c0:3d)", "share": 0.99}]}}
    (line,) = attention(d, {"apps"})
    assert "13% of identified traffic, nearly all from Unnamed device (…c0:3d)" in line


def test_download_sentence_follows_the_switch():
    from app.text import summary
    d = {"wans": [], "failovers": 0, "wan_down": 600e9, "wan_up": 246e9, "clients_peak": None, "blocks_in": None}
    covered = {"prom": True, "loki": True, "rates": True}
    assert any("downloaded 600 GB" in s for s in summary(d, {}, "week", covered, {"downloaded", "summary"}))
    assert not any("downloaded" in s for s in summary(d, {}, "week", covered, {"summary"}))


def test_category_spike_and_jump_name_the_device():
    import datetime as dt

    from app.text import category_alerts
    day = dt.datetime(2026, 11, 13)
    bd = {
        "spikes": [{"category": "Network Protocols", "day": day, "bytes": 112e9, "times": 3.6,
                    "device": "NAS (…12:34)", "device_share": 0.95}],
        "rows": [
            {"name": "Network Protocols", "per_day": 40e9, "per_day_before": 10e9, "share": 0.4,
             "top_device": "NAS (…12:34)", "top_device_share": 0.8},
            {"name": "Media Streaming", "per_day": 12e9, "per_day_before": 4e9, "share": 0.1,
             "top_device": "TV (…ab:cd)", "top_device_share": 0.4},
            {"name": "Web Services", "per_day": 9e9, "per_day_before": 8e9, "share": 0.1,
             "top_device": None, "top_device_share": None},
        ],
    }
    assert category_alerts(bd, "week") == [
        "Network Protocols traffic jumped to 112 GB on Fri 13 Nov, 4× a usual day, nearly all from NAS (…12:34)",
        "Media Streaming traffic was 3× last week's, 12.0 GB a day, mostly from TV (…ab:cd) (40%)",
    ]


def test_a_renamed_access_point_is_one_row_under_its_latest_name():
    from app.collect import _by_device, device_names

    class S:
        def prom(self, q):
            assert 'type="uap"' in q
            return [
                {"metric": {"mac": "02:00:00:00:00:01", "name": "U6 Pro"}, "value": [0, "1000"]},
                {"metric": {"mac": "02:00:00:00:00:01", "name": "Hall"}, "value": [0, "2000"]},
                {"metric": {"mac": "02:00:00:00:00:02", "name": "Office"}, "value": [0, "2000"]},
            ]

    names, latest = device_names(S(), "uap")
    assert latest == {"02:00:00:00:00:01": "Hall", "02:00:00:00:00:02": "Office"}
    assert _by_device({"U6 Pro": 5.0, "Hall": 7.0, "Office": 1.0, "Old AP": 2.0}, names) == {
        "02:00:00:00:00:01": 12.0, "02:00:00:00:00:02": 1.0, "Old AP": 2.0}
