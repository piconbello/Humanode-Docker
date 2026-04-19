from __future__ import annotations

import io
from urllib.parse import quote

DEFAULT_WEBAPP_BASE = "https://webapp.mainnet.stages.humanode.io"


def compose_bioauth_url(tunnel_wss_url: str, webapp_base: str = DEFAULT_WEBAPP_BASE) -> str:
    return f"{webapp_base.rstrip('/')}/open?url={quote(tunnel_wss_url, safe='')}"


def qr_png_bytes(url: str, box_size: int = 8, border: int = 2) -> bytes:
    import qrcode

    qr = qrcode.QRCode(box_size=box_size, border=border)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
