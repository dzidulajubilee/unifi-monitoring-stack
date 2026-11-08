from app.text import _join, _times, _who, change, fbits, fbytes, fpct, trend_change


def test_formatting():
    assert fbytes(600e9) == "600 GB" and fbytes(1.4e12) == "1.4 TB" and fbytes(None) == "–"
    assert fbits(41.1e6) == "41.1 Mbps"
    assert fpct(0.134) == "13%"


def test_change_wording():
    assert change(110, 100, "", "week", True) == "+10% vs last week"
    assert change(3, 5, "", "week", True) == "2 fewer vs last week"  # small numbers stay numbers
    assert change(5, 5, "", "week", True) == "same as last week"
    assert change(5, 4, "", "week", False) == "no earlier week to compare"


def test_trend_change_wording():
    m = {"current": 0, "avg": 3.2, "n_before": 5, "unit": "num"}
    assert trend_change(m, "weeks") == "0 vs an average of 3.2"
    m = {"current": 137510, "avg": 143317, "n_before": 4, "unit": "num"}
    assert trend_change(m, "weeks") == "−4% vs the 4-week average"
    assert trend_change({"current": None, "avg": 1, "n_before": 1, "unit": "num"}, "weeks") == "not enough data for this week"


def test_people_words():
    assert _times(1) == "once" and _times(2) == "twice" and _times(5) == "5 times"
    assert _join(["a"]) == "a" and _join(["a", "b", "c"]) == "a, b and c"
    assert _who("16:f9:c4:9d:c0:3d") == "Unnamed device (…c0:3d)" and _who("iPhone") == "iPhone"


def test_delta_for_table_cells():
    from app.text import delta
    assert [delta(25, 20), delta(12, 9), delta(9, 9), delta(5, 0), delta(5, None), delta(30, 20, covered=False)] == [
        "+25%", "+3", "same", "new", "–", "–"]
