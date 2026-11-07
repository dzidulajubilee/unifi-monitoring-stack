from app.text import attention

BASE = {"aps": [], "gw_reboots": 0, "mem_end": None, "mem_start": None, "mem_max": None, "threats_open": 0,
        "threats_high": 0, "logins_outside": 0, "threat_devices": []}


def ap(name, retries=0.0, signal=None):
    return {"name": name, "reboots": 0, "u24": 0.2, "retries": retries, "signal": signal}


def test_access_point_problems_are_grouped_worst_first():
    d = {**BASE, "aps": [ap("ROOM 1", 0.28), ap("ROOM 4", 0.89, -81), ap("NOC", 0.01)]}
    items = attention(d, {"aps"})
    assert items[0].startswith("Access points resending many Wi-Fi packets") and "ROOM 4 89% and ROOM 1 28%" in items[0]
    assert "NOC" not in items[0]
    assert items[1] == ("Devices averaged a weak signal on ROOM 4 (-81 dBm); they're too far from the access point "
                        "or behind walls")


def test_sections_switched_off_raise_nothing():
    d = {**BASE, "aps": [ap("ROOM 4", 0.89)], "gw_reboots": 2}
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
    covered = {"prom": True, "loki": True}
    assert any("downloaded 600 GB" in s for s in summary(d, {}, "week", covered, {"downloaded", "summary"}))
    assert not any("downloaded" in s for s in summary(d, {}, "week", covered, {"summary"}))
