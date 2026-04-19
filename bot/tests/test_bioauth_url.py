from hmnd_bot.bioauth_url import DEFAULT_WEBAPP_BASE, compose_bioauth_url, qr_png_bytes


def test_compose_url_url_encodes_wss():
    url = compose_bioauth_url("wss://abc.ngrok-free.app")
    assert url.startswith(DEFAULT_WEBAPP_BASE + "/open?url=")
    assert "wss%3A%2F%2Fabc.ngrok-free.app" in url


def test_compose_url_custom_base():
    url = compose_bioauth_url("wss://abc.ngrok.app", webapp_base="https://webapp.example.com")
    assert url.startswith("https://webapp.example.com/open?url=")


def test_compose_url_strips_trailing_slash():
    url = compose_bioauth_url("wss://x", webapp_base="https://host/")
    assert url.startswith("https://host/open?url=")


def test_qr_png_bytes_returns_png():
    png = qr_png_bytes("https://example.com")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 100
