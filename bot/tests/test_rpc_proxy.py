import json

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from hmnd_bot import rpc_proxy
from hmnd_bot.rpc_proxy import ALLOWED, build_app, check

NODE_UNSAFE = {
    "author_insertKey", "author_rotateKeys", "author_hasKey", "author_hasSessionKeys", "authorExt_setKeys",
    "bioauth_enroll", "bioauth_enrollV2", "bioauth_enrollStepV1", "bioauth_authenticate",
    "bioauth_authenticateV2", "bioauth_authenticateStepV1", "author_removeExtrinsic", "babe_epochAuthorship",
    "offchain_localStorageGet", "offchain_localStorageSet", "state_getPairs", "state_queryStorage",
    "state_traceBlock", "system_addLogFilter", "system_resetLogFilter", "system_addReservedPeer",
    "system_removeReservedPeer", "system_peers", "system_unstable_networkState", "system_dryRun",
    "system_dryRunAt", "state_subscribeStorage",
}


def call(method, req_id=1):
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": []}


def test_allowlist_excludes_unsafe_and_validator_methods():
    assert not ALLOWED & NODE_UNSAFE
    assert not {m for m in ALLOWED if m.startswith(("bioauth_", "author", "offchain_", "babe_", "grandpa_", "txpool_"))}
    assert "eth_sendTransaction" not in ALLOWED


def test_check_single():
    assert check(call("eth_blockNumber")) is None
    err = check(call("author_insertKey", 7))
    assert err == {"jsonrpc": "2.0", "id": 7, "error": {"code": -32601, "message": "Method not allowed"}}


@pytest.mark.parametrize("payload", [{}, {"method": 1}, "eth_call", 5, None, [], [1], {"params": []}])
def test_check_rejects_malformed(payload):
    assert check(payload) is not None


def test_check_batch_is_all_or_nothing():
    assert check([call("eth_chainId", 1), call("eth_blockNumber", 2)]) is None
    errs = check([call("eth_chainId", 1), call("bioauth_enroll", 2)])
    assert [e["id"] for e in errs] == [1, 2]
    assert all(e["error"]["code"] == -32601 for e in errs)


@pytest.fixture
async def stack():
    seen = []

    async def http(request):
        body = await request.json()
        seen.append(body)
        return web.json_response({"jsonrpc": "2.0", "id": body.get("id") if isinstance(body, dict) else None, "result": "ok"})

    async def ws(request):
        sock = web.WebSocketResponse()
        await sock.prepare(request)
        async for msg in sock:
            body = json.loads(msg.data)
            seen.append(body)
            await sock.send_str(json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": "ok"}))
        return sock

    async def root(request):
        if request.method == "GET":
            return await ws(request)
        return await http(request)

    upstream_app = web.Application()
    upstream_app.router.add_route("*", "/", root)
    upstream = TestServer(upstream_app)
    await upstream.start_server()
    proxy = TestServer(build_app(str(upstream.make_url("/")), str(upstream.make_url("/")).replace("http", "ws", 1)))
    await proxy.start_server()
    async with aiohttp.ClientSession() as session:
        yield session, proxy, seen
    await proxy.close()
    await upstream.close()


async def test_http_forwards_allowed_and_blocks_unsafe(stack):
    session, proxy, seen = stack
    async with session.post(proxy.make_url("/"), json=call("eth_chainId")) as r:
        assert (await r.json())["result"] == "ok"
    async with session.post(proxy.make_url("/"), json=call("author_rotateKeys")) as r:
        assert (await r.json())["error"]["code"] == -32601
    async with session.post(proxy.make_url("/"), data=b"\xff\xfe\xfa") as r:
        assert (await r.json())["error"]["code"] == -32700
    async with session.post(proxy.make_url("/"), data="{not json") as r:
        assert (await r.json())["error"]["code"] == -32700
    assert [b["method"] for b in seen] == ["eth_chainId"]


async def test_http_duplicate_method_key_forwards_checked_value(stack):
    session, proxy, seen = stack
    raw = '{"jsonrpc":"2.0","id":1,"method":"author_insertKey","method":"eth_chainId","params":[]}'
    async with session.post(proxy.make_url("/"), data=raw) as r:
        assert (await r.json())["result"] == "ok"
    assert seen == [call("eth_chainId")]


async def test_ws_forwards_allowed_and_blocks_unsafe(stack):
    session, proxy, seen = stack
    async with session.ws_connect(proxy.make_url("/")) as ws:
        await ws.send_str(json.dumps(call("bioauth_enroll", 1)))
        assert json.loads((await ws.receive()).data)["error"]["code"] == -32601
        await ws.send_str(json.dumps(call("eth_blockNumber", 2)))
        assert json.loads((await ws.receive()).data) == {"jsonrpc": "2.0", "id": 2, "result": "ok"}
        await ws.send_bytes(b"\x00")
        assert (await ws.receive()).type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING)
    assert [b["method"] for b in seen] == ["eth_blockNumber"]


async def test_ws_connections_are_capped(stack):
    session, proxy, _ = stack
    slots = proxy.app[rpc_proxy.WS_SLOTS]
    for _ in range(rpc_proxy.MAX_WS):
        await slots.acquire()
    with pytest.raises(aiohttp.WSServerHandshakeError) as e:
        await session.ws_connect(proxy.make_url("/"))
    assert e.value.status == 503
    slots.release()
    async with session.ws_connect(proxy.make_url("/")) as ws:
        await ws.send_str(json.dumps(call("eth_chainId")))
        assert json.loads((await ws.receive()).data)["result"] == "ok"
