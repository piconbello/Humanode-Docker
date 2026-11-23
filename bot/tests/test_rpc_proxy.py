import asyncio
import json
from pathlib import Path

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


VPK = "hmt66i9y3jqAydqAB4FVeDdh64es98unwZbZXaNEPGWJdHrKc"
ACCOUNT = "f88c8ee2ce0cd14890971739280c738a3c41e304dc2b910a2f5361b26eea2a5a"
WEBAPP_CALLS = [
    "rpc_methods", "chain_getBlockHash", "state_getMetadata", "state_getRuntimeVersion",
    "state_subscribeRuntimeVersion", "state_unsubscribeRuntimeVersion", "system_chain", "system_properties",
    "system_health", "system_syncState", "chain_subscribeAllHeads", "chain_unsubscribeAllHeads",
    "bioauth_status", "bioauth_getFacetecDeviceSdkParamsV2", "bioauth_enrollStepV1",
    "bioauth_authenticateStepV1", "authorExt_getValidatorPublicKey", "author_submitAndWatchExtrinsic",
    "author_unwatchExtrinsic", "chain_getBlock", "chain_getHeader", "state_getStorage",
    "state_queryStorageAt", "state_unsubscribeStorage",
]


def test_ss58_account_decodes_and_checks_checksum():
    assert rpc_proxy.ss58_account(VPK) == ACCOUNT
    with pytest.raises(ValueError):
        rpc_proxy.ss58_account(VPK[:-1] + "d")


def test_tunnel_policy_allows_webapp_and_blocks_admin():
    for m in WEBAPP_CALLS:
        assert rpc_proxy.tunnel_allowed(call(m), False), m
    for m in rpc_proxy.TUNNEL_BLOCKED:
        assert not rpc_proxy.tunnel_allowed(call(m), True), m
    for m in rpc_proxy.KEY_SETUP:
        assert not rpc_proxy.tunnel_allowed(call(m), False)
        assert rpc_proxy.tunnel_allowed(call(m), True)
    assert not rpc_proxy.tunnel_allowed({"jsonrpc": "2.0", "id": 1, "method": "state_subscribeStorage", "params": []}, True)
    assert rpc_proxy.tunnel_allowed({"jsonrpc": "2.0", "id": 1, "method": "state_subscribeStorage", "params": [["0x26aa"]]}, True)
    assert NODE_UNSAFE - rpc_proxy.TUNNEL_BLOCKED - rpc_proxy.KEY_SETUP == {
        "bioauth_enroll", "bioauth_enrollV2", "bioauth_enrollStepV1", "bioauth_authenticate",
        "bioauth_authenticateV2", "bioauth_authenticateStepV1", "state_subscribeStorage"}



def test_classified_methods_exist_on_node():
    node = set(Path(__file__).parents[2].joinpath("tests/image/rpc_methods.txt").read_text().split())
    assert not (NODE_UNSAFE | ALLOWED | rpc_proxy.TUNNEL_BLOCKED | rpc_proxy.KEY_SETUP) - node

@pytest.fixture
async def tunnel_stack(monkeypatch):
    monkeypatch.delenv("TUNNEL_KEY_SETUP", raising=False)
    node = {"next_keys": [], "seen": [], "fail": False, "syncing": False}

    async def http(request):
        body = await request.json()
        node["seen"].append(body["method"])
        if node["fail"]:
            return web.json_response({"jsonrpc": "2.0", "id": body["id"], "error": {"code": -1, "message": "x"}})
        result = {"authorExt_getValidatorPublicKey": VPK,
                  "system_health": {"peers": 5, "isSyncing": node["syncing"], "shouldHavePeers": True},
                  "state_getKeysPaged": node["next_keys"]}.get(body["method"], "ok")
        return web.json_response({"jsonrpc": "2.0", "id": body["id"], "result": result})

    upstream_app = web.Application()
    upstream_app.router.add_post("/", http)
    upstream = TestServer(upstream_app)
    await upstream.start_server()
    url = str(upstream.make_url("/"))
    proxy = TestServer(build_app(url, url.replace("http", "ws", 1), policy=rpc_proxy.tunnel_policy))
    await proxy.start_server()
    async with aiohttp.ClientSession() as session:
        yield session, proxy, node, monkeypatch
    await proxy.close()
    await upstream.close()


async def _post(session, proxy, method):
    async with session.post(proxy.make_url("/"), json=call(method)) as r:
        return await r.json()


async def test_tunnel_key_setup_open_until_session_keys_are_set(tunnel_stack):
    session, proxy, node, monkeypatch = tunnel_stack
    assert (await _post(session, proxy, "authorExt_setKeys"))["result"] == "ok"
    node["next_keys"] = [rpc_proxy.SESSION_NEXT_KEYS + "17072cf263a40a25" + ACCOUNT]
    assert (await _post(session, proxy, "author_rotateKeys"))["error"]["code"] == -32601
    assert (await _post(session, proxy, "bioauth_authenticateStepV1"))["result"] == "ok"
    monkeypatch.setenv("TUNNEL_KEY_SETUP", "true")
    assert (await _post(session, proxy, "authorExt_setKeys"))["result"] == "ok"


async def test_tunnel_key_setup_closed_while_syncing(tunnel_stack):
    session, proxy, node, _ = tunnel_stack
    node["syncing"] = True
    assert (await _post(session, proxy, "authorExt_setKeys"))["error"]["code"] == -32601
    node["syncing"] = False
    assert (await _post(session, proxy, "authorExt_setKeys"))["result"] == "ok"


async def test_tunnel_key_setup_closed_when_check_fails(tunnel_stack):
    session, proxy, node, _ = tunnel_stack
    node["fail"] = True
    assert (await _post(session, proxy, "authorExt_setKeys"))["error"]["code"] == -32601
    assert (await _post(session, proxy, "author_insertKey"))["error"]["code"] == -32601


async def test_tunnel_key_setup_pages_through_next_keys(tunnel_stack):
    session, proxy, node, monkeypatch = tunnel_stack
    other = rpc_proxy.SESSION_NEXT_KEYS + "00" * 8 + "11" * 32
    node["next_keys"] = [other] * 1000
    pages = []
    real = rpc_proxy._upstream_call

    async def paged(app, method, params):
        if method == "state_getKeysPaged":
            pages.append(params[2])
            return [other] * 1000 if params[2] is None else [rpc_proxy.SESSION_NEXT_KEYS + "00" * 8 + ACCOUNT]
        return await real(app, method, params)

    monkeypatch.setattr(rpc_proxy, "_upstream_call", paged)
    assert (await _post(session, proxy, "authorExt_setKeys"))["error"]["code"] == -32601
    assert pages == [None, other]


def test_fix_request_line():
    assert rpc_proxy.fix_request_line(b"GET 6724-x.ws1.htunnel.app HTTP/1.1\r\n") == b"GET / HTTP/1.1\r\n"
    assert rpc_proxy.fix_request_line(b"POST /rpc HTTP/1.1\r\n") == b"POST /rpc HTTP/1.1\r\n"
    assert rpc_proxy.fix_request_line(b"garbage\r\n") == b"garbage\r\n"


async def test_tunnel_relay_accepts_hostname_request_target(tunnel_stack, unused_tcp_port_factory):
    _, proxy, _, _ = tunnel_stack
    port = unused_tcp_port_factory()
    server = await asyncio.start_server(lambda r, w: rpc_proxy._relay(r, w, proxy.port), "127.0.0.1", port)
    async with server:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        body = json.dumps(call("author_insertKey")).encode()
        writer.write(b"POST 6724-x.ws1.htunnel.app HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                     b"Connection: close\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        await writer.drain()
        reply = await reader.read()
        writer.close()
    assert reply.startswith(b"HTTP/1.1 200")
    assert b"Method not allowed" in reply
