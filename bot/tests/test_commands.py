from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from hmnd_bot.commands import build_router
from hmnd_bot.node import BlockInfo, Health, NodeUnavailable
from hmnd_bot.tunnel import TunnelQuotaExceeded

CHAT_ID = 4242


@pytest.fixture(autouse=True)
def _stub_qr(monkeypatch):
    monkeypatch.setattr("hmnd_bot.commands.qr_png_bytes", lambda url, **kw: b"png")


class FakeCatchup:
    def __init__(self, behind=False, lag=None):
        self.is_behind = behind
        self.lag = lag


class FakeNode:
    def __init__(self, raise_unavailable=False):
        self.raise_unavailable = raise_unavailable

    async def system_health(self):
        if self.raise_unavailable:
            raise NodeUnavailable("down")
        return Health(peers=7, is_syncing=True, should_have_peers=True)

    async def best_block(self):
        if self.raise_unavailable:
            raise NodeUnavailable("down")
        return BlockInfo(number=12345, hash=None)


class FakeTunnel:
    def __init__(self, error=None):
        self.error = error
        self.starts = 0

    async def start(self):
        self.starts += 1
        if self.error:
            raise self.error
        return "wss://x.ngrok-free.app"


class FakeMessage:
    def __init__(self):
        self.answer = AsyncMock()
        self.answer_photo = AsyncMock()


def _handler(catchup, node, tunnel):
    router = build_router(
        chat_id=CHAT_ID,
        node=node,
        tunnel=tunnel,
        catchup=catchup,
        webapp_base="https://webapp.example",
    )
    return router.message.handlers[0].callback


async def test_link_returns_qr_when_current():
    tunnel = FakeTunnel()
    handler = _handler(FakeCatchup(behind=False), FakeNode(), tunnel)
    msg = FakeMessage()
    await handler(msg)
    msg.answer_photo.assert_called_once()
    assert tunnel.starts == 1


async def test_link_warns_and_withholds_link_when_behind():
    tunnel = FakeTunnel()
    handler = _handler(FakeCatchup(behind=True, lag=timedelta(hours=4)), FakeNode(), tunnel)
    msg = FakeMessage()
    await handler(msg)
    msg.answer_photo.assert_not_called()
    assert tunnel.starts == 0
    text = msg.answer.call_args[0][0]
    assert "syncing" in text.lower()
    assert "4h" in text
    assert "12345" in text


async def test_link_reports_unavailable_chain_view():
    handler = _handler(FakeCatchup(behind=True, lag=None), FakeNode(), FakeTunnel())
    msg = FakeMessage()
    await handler(msg)
    assert "unavailable" in msg.answer.call_args[0][0].lower()


async def test_link_reports_rpc_unreachable_while_behind():
    handler = _handler(FakeCatchup(behind=True), FakeNode(raise_unavailable=True), FakeTunnel())
    msg = FakeMessage()
    await handler(msg)
    assert "unreachable" in msg.answer.call_args[0][0].lower()
    msg.answer_photo.assert_not_called()


async def test_link_preserves_tunnel_error_reply_when_current():
    tunnel = FakeTunnel(error=TunnelQuotaExceeded("quota"))
    handler = _handler(FakeCatchup(behind=False), FakeNode(), tunnel)
    msg = FakeMessage()
    await handler(msg)
    assert "quota" in msg.answer.call_args[0][0].lower()
    msg.answer_photo.assert_not_called()
