import io

from app import charts, config, web

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
SVG = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 10"/>'


def admin():
    c = web.app.test_client()
    with c.session_transaction() as s:
        s.update(user="Admin", login="admin", admin=True, csrf="t")
    return c


def post_settings(c, **extra):
    data = {"csrf": "t", "timezone": "Africa/Accra", "sec_summary": "on", "sum_summary": "on", "hour": "7"}
    data.update(extra)
    return c.post("/settings", data=data, content_type="multipart/form-data")


def test_theme_defaults_to_blue_and_ignores_unknown_names():
    s = config.load_settings()
    assert config.theme(s) == config.THEMES["blue"]
    s["general"]["theme"] = "neon"
    assert config.theme(s) == config.THEMES["blue"]


def test_blue_keeps_the_original_network_colours():
    assert charts.net_palette(config.THEMES["blue"]["accent"]) == charts.NET_COLORS


def test_network_palette_leads_with_the_accent_and_drops_look_alikes():
    pal = charts.net_palette(config.THEMES["green"]["accent"])
    assert pal[0] == config.THEMES["green"]["accent"]
    assert "#008300" not in pal and "#1baf7a" not in pal  # both greens would read as the first network
    assert "#2a78d6" in pal
    assert len(charts.net_palette(config.THEMES["graphite"]["accent"])) == len(charts.NET_COLORS) + 1


def test_charts_draw_in_the_accent():
    svg = charts.svg_minibars(
        {"values": [1, 2], "labels": ["a", "b"], "unit": "n", "title": "Clients"}, accent="#0e8585"
    )
    assert "#0e8585" in svg and "#2a78d6" not in svg


def test_logo_type_reads_the_file_not_its_name():
    assert config.logo_type(PNG) == "png"
    assert config.logo_type(b"\xff\xd8\xff\xe0rest") == "jpg"
    assert config.logo_type(SVG) == "svg"
    assert config.logo_type(b"\xef\xbb\xbf  <svg/>") == "svg"
    assert config.logo_type(b"<html><script>") is None
    assert config.logo_type(b"GIF89a") is None


def test_settings_saves_the_theme_and_ignores_a_made_up_one():
    c = admin()
    post_settings(c, theme="teal")
    assert config.load_settings()["general"]["theme"] == "teal"
    post_settings(c, theme="neon")
    assert config.load_settings()["general"]["theme"] == "teal"
    post_settings(c, theme="blue")


def test_logo_upload_preview_and_removal():
    c = admin()
    post_settings(c, logo=(io.BytesIO(SVG), "brand.svg"))
    assert config.logo_path() == config.OUT / "logo.svg"
    r = c.get("/settings/logo")
    assert r.status_code == 200 and r.mimetype == "image/svg+xml"
    assert "default-src 'none'" in r.headers["Content-Security-Policy"]
    assert 'alt="Current logo"' in c.get("/settings").get_data(as_text=True)

    post_settings(c, logo=(io.BytesIO(PNG), "brand.png"))  # a new upload replaces the old one
    assert config.logo_path() == config.OUT / "logo.png"
    assert not (config.OUT / "logo.svg").exists()

    post_settings(c, remove_logo="on")
    assert config.logo_path() is None
    assert c.get("/settings/logo").status_code == 404


def test_logo_rejects_other_files_and_big_ones():
    c = admin()
    post_settings(c, logo=(io.BytesIO(b"<html>hi</html>"), "logo.svg"))
    assert config.logo_path() is None
    post_settings(c, logo=(io.BytesIO(PNG + b"\x00" * config.LOGO_MAX_BYTES), "big.png"))
    assert config.logo_path() is None


def test_logo_preview_is_for_admins_only():
    c = web.app.test_client()
    with c.session_transaction() as s:
        s.update(user="Ama", login="ama", admin=False, csrf="t")
    assert c.get("/settings/logo").status_code == 403
