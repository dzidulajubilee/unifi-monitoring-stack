from app.layout import problems
from weasyprint import HTML

PAGE = "<style>@page { size: A4; margin: 20mm } section { break-inside: avoid } h3 { break-after: avoid }</style>"


def lines(n, word="row"):
    return "".join(f"<p>{word} {i}</p>" for i in range(n))


def test_a_kept_together_block_too_tall_for_page_one_is_caught():
    html = PAGE + "<h1>Weekly report</h1><section>" + lines(60) + "</section>"
    assert any(p.startswith("page 1 is") for p in problems(HTML(string=html).render()))


def test_a_heading_left_at_the_bottom_of_a_page_is_caught():
    # as when the page flow overrides the heading's "break-after: avoid"
    html = PAGE + lines(20, "filler") + "<h3 style='break-after: page'>IP address conflicts</h3>" + lines(10)
    found = problems(HTML(string=html).render())
    assert any("IP address conflicts" in p for p in found), found


def test_a_heading_with_one_line_left_under_it_is_caught():
    html = PAGE + lines(20, "filler") + "<h3>IP address conflicts</h3><p style='break-after: page'>first</p>" + lines(5)
    found = problems(HTML(string=html).render())
    assert any("IP address conflicts" in p and "1 line" in p for p in found), found


def test_a_chart_counts_as_content_under_a_heading():
    svg = "<svg width='600' height='300' style='display:block'><rect width='600' height='300'/></svg>"
    html = PAGE + lines(25, "filler") + "<h3>Traffic per day</h3>" + svg + "<p style='break-after: page'>key</p>" + lines(5)
    assert problems(HTML(string=html).render()) == []


def test_a_short_block_that_fits_at_the_bottom_is_fine():
    html = PAGE + lines(20, "filler") + "<div><h3>Devices in threats</h3><p style='break-after: page'>one</p></div>" + lines(5)
    assert problems(HTML(string=html).render()) == []


def test_content_wider_than_the_page_is_caught():
    html = PAGE + "<table class='wide'><tr><td style='white-space:nowrap'>" + "x" * 400 + "</td></tr></table>"
    found = problems(HTML(string=html).render())
    assert any("wider than the page" in p for p in found), found


def test_a_chart_too_tall_for_a_page_is_caught():
    svg = "<svg width='400' height='2000' style='display:block'><rect width='400' height='2000'/></svg>"
    found = problems(HTML(string=PAGE + svg).render())
    assert any("past the bottom" in p for p in found), found


def test_a_page_that_flows_on_is_fine():
    html = PAGE + "<h1>Weekly report</h1><section style='break-inside: auto'>" + lines(80) + "</section>"
    assert problems(HTML(string=html).render()) == []


def test_a_short_page_before_a_section_moved_down_whole_is_fine():
    # each section stays together, so one that doesn't fit under the previous starts the next page
    html = PAGE + "<section>" + lines(12) + "</section><section><h3>Devices</h3>" + lines(35) + "</section>"
    assert problems(HTML(string=html).render()) == []



def test_the_end_of_a_long_section_before_a_new_one_is_fine():
    html = PAGE + "<section>" + lines(32) + "</section><section><h3>Devices</h3>" + lines(24) + "</section>"
    assert problems(HTML(string=html).render()) == []



def test_a_line_or_two_left_at_the_end_of_a_section_is_caught():
    html = PAGE + "<section>" + lines(26) + "</section><section><h3>Devices</h3>" + lines(24) + "</section>"
    found = problems(HTML(string=html).render())
    assert any(p.startswith("page 2 is") for p in found), found
