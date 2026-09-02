from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from hmnd_bot.node import (
    BioauthStatus,
    NodeClient,
    NodeRpcError,
    NodeUnavailable,
    SyncState,
)


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


NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def _encode_ts(moment: datetime) -> str:
    millis = int(moment.timestamp() * 1000)
    return "0x" + millis.to_bytes(8, "little").hex()


@pytest.mark.parametrize("lag", [timedelta(seconds=30), timedelta(hours=4)])
async def test_best_block_age_reports_lag(client, lag):
    raw = _encode_ts(NOW - lag)
    with patch.object(client, "call", new=AsyncMock(return_value=raw)):
        age = await client.best_block_age(NOW)
    assert age is not None
    assert abs(age - lag) < timedelta(milliseconds=2)


async def test_best_block_age_null_storage_is_unreadable(client):
    with patch.object(client, "call", new=AsyncMock(return_value=None)):
        assert await client.best_block_age(NOW) is None


async def test_best_block_age_undecodable_is_unreadable(client):
    with patch.object(client, "call", new=AsyncMock(return_value="0xdeadbeef")):
        assert await client.best_block_age(NOW) is None


async def test_best_block_age_future_timestamp_is_unreadable(client):
    raw = _encode_ts(NOW + timedelta(hours=1))
    with patch.object(client, "call", new=AsyncMock(return_value=raw)):
        assert await client.best_block_age(NOW) is None


async def test_best_block_age_absurdly_old_is_unreadable(client):
    raw = _encode_ts(NOW - timedelta(days=4000))
    with patch.object(client, "call", new=AsyncMock(return_value=raw)):
        assert await client.best_block_age(NOW) is None


async def test_best_block_age_propagates_transport_error(client):
    with patch.object(client, "call", new=AsyncMock(side_effect=NodeUnavailable("down"))):
        with pytest.raises(NodeUnavailable):
            await client.best_block_age(NOW)


async def test_best_block_age_propagates_rpc_error(client):
    with patch.object(client, "call", new=AsyncMock(side_effect=NodeRpcError("no method"))):
        with pytest.raises(NodeRpcError):
            await client.best_block_age(NOW)


async def test_sync_state_reports_gap(client):
    payload = {"startingBlock": 0, "currentBlock": 1000, "highestBlock": 1500}
    with patch.object(client, "call", new=AsyncMock(return_value=payload)):
        st = await client.sync_state()
    assert st == SyncState(current=1000, highest=1500, gap=500)


async def test_sync_state_equal_heights_gives_zero_gap(client):
    payload = {"currentBlock": 2000, "highestBlock": 2000}
    with patch.object(client, "call", new=AsyncMock(return_value=payload)):
        st = await client.sync_state()
    assert st.gap == 0


async def test_sync_state_missing_highest_falls_back_to_current(client):
    payload = {"currentBlock": 2000}
    with patch.object(client, "call", new=AsyncMock(return_value=payload)):
        st = await client.sync_state()
    assert st.gap == 0


async def test_sync_state_behind_tip_never_reports_negative_gap(client):
    payload = {"currentBlock": 2100, "highestBlock": 2000}
    with patch.object(client, "call", new=AsyncMock(return_value=payload)):
        st = await client.sync_state()
    assert st.gap == 0


async def test_sync_state_propagates_transport_error(client):
    with patch.object(client, "call", new=AsyncMock(side_effect=NodeUnavailable("down"))):
        with pytest.raises(NodeUnavailable):
            await client.sync_state()
