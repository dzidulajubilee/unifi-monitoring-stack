from weasyprint import HTML

from app.layout import problems

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


def test_a_page_that_flows_on_is_fine():
    html = PAGE + "<h1>Weekly report</h1><section style='break-inside: auto'>" + lines(80) + "</section>"
    assert problems(HTML(string=html).render()) == []
