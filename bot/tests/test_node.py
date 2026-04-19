from unittest.mock import AsyncMock, patch

import pytest

from hmnd_bot.node import BioauthStatus, NodeClient


@pytest.fixture
def client():
    return NodeClient("http://127.0.0.1:9944")


async def test_call_dispatches_jsonrpc(client):
    with patch.object(client, "_session") as session:
        cm = AsyncMock()
        cm.json = AsyncMock(return_value={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
        cm.raise_for_status = lambda: None
        post_cm = AsyncMock()
        post_cm.__aenter__.return_value = cm
        post_cm.__aexit__.return_value = False
        session.post.return_value = post_cm
        session.closed = False

        result = await client.call("foo", [1, 2])
        assert result == {"ok": True}
        session.post.assert_called_once()


async def test_best_block_decodes_hex(client):
    with patch.object(client, "call", new=AsyncMock(return_value={"number": "0x2a"})):
        b = await client.best_block()
        assert b.number == 42


async def test_bioauth_status_unknown(client):
    with patch.object(client, "call", new=AsyncMock(return_value="Unknown")):
        s = await client.bioauth_status()
        assert isinstance(s, BioauthStatus)
        assert s.is_active is False
        assert s.expires_at_ms is None


async def test_bioauth_status_active(client):
    payload = {"Active": {"expires_at": 1700000000000}}
    with patch.object(client, "call", new=AsyncMock(return_value=payload)):
        s = await client.bioauth_status()
        assert s.is_active is True
        assert s.expires_at_ms == 1700000000000


def test_ws_url_rewritten_to_http():
    c = NodeClient("ws://127.0.0.1:9944")
    assert c._url == "http://127.0.0.1:9944"
