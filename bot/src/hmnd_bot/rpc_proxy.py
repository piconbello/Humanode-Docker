from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
from typing import Any, Awaitable, Callable

import aiohttp
from aiohttp import web

logger = logging.getLogger("hmnd_bot.rpc_proxy")

UPSTREAM_HTTP = "http://127.0.0.1:9944"
UPSTREAM_WS = "ws://127.0.0.1:9944"
MAX_SIZE = 15 * 1024 * 1024
MAX_UPSTREAM = 20
MAX_WS = 10

SESSION = web.AppKey("session", aiohttp.ClientSession)
WS_SLOTS = web.AppKey("ws_slots", asyncio.Semaphore)
UPSTREAM_HTTP_KEY = web.AppKey("upstream_http", str)
UPSTREAM_WS_KEY = web.AppKey("upstream_ws", str)
POLICY = web.AppKey("policy", object)
TUNNEL_PORT = 9946
TUNNEL_APP_PORT = 9947

ALLOWED = frozenset({
    "eth_blockNumber", "eth_call", "eth_chainId", "eth_estimateGas", "eth_feeHistory",
    "eth_gasPrice", "eth_getBalance", "eth_getBlockByHash", "eth_getBlockByNumber",
    "eth_getBlockTransactionCountByHash", "eth_getBlockTransactionCountByNumber",
    "eth_getCode", "eth_getFilterChanges", "eth_getFilterLogs", "eth_getLogs",
    "eth_getStorageAt", "eth_getTransactionByBlockHashAndIndex",
    "eth_getTransactionByBlockNumberAndIndex", "eth_getTransactionByHash",
    "eth_getTransactionCount", "eth_getTransactionReceipt", "eth_maxPriorityFeePerGas",
    "eth_newBlockFilter", "eth_newFilter", "eth_newPendingTransactionFilter",
    "eth_protocolVersion", "eth_sendRawTransaction", "eth_subscribe", "eth_syncing",
    "eth_uninstallFilter", "eth_unsubscribe",
    "net_listening", "net_version", "web3_clientVersion", "web3_sha3",
    "chain_getBlock", "chain_getBlockHash", "chain_getFinalisedHead", "chain_getFinalizedHead",
    "chain_getHead", "chain_getHeader", "chain_subscribeFinalisedHeads",
    "chain_subscribeFinalizedHeads", "chain_subscribeNewHead", "chain_subscribeNewHeads",
    "chain_unsubscribeFinalisedHeads", "chain_unsubscribeFinalizedHeads",
    "chain_unsubscribeNewHead", "chain_unsubscribeNewHeads",
    "state_call", "state_callAt", "state_getMetadata", "state_getRuntimeVersion",
    "state_getStorage", "state_getStorageAt",
    "system_chain", "system_chainType", "system_health", "system_name",
    "system_properties", "system_syncState", "system_version",
})


TUNNEL_BLOCKED = frozenset({
    "author_insertKey", "author_hasKey", "author_hasSessionKeys", "author_removeExtrinsic",
    "babe_epochAuthorship", "offchain_localStorageGet", "offchain_localStorageSet",
    "state_getPairs", "state_queryStorage", "state_traceBlock",
    "system_addLogFilter", "system_resetLogFilter", "system_addReservedPeer",
    "system_removeReservedPeer", "system_peers", "system_unstable_networkState",
    "system_dryRun", "system_dryRunAt",
})
KEY_SETUP = frozenset({"author_rotateKeys", "authorExt_setKeys"})
SESSION_NEXT_KEYS = "0xcec5070d609dd3497f72bde07fc96ba04c014e6bf8b8c2c011e7290b85696bb3"
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def ss58_account(address: str) -> str:
    n = 0
    for c in address:
        n = n * 58 + _B58.index(c)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    data, checksum = raw[:-2], raw[-2:]
    if len(data) < 33 or hashlib.blake2b(b"SS58PRE" + data, digest_size=64).digest()[:2] != checksum:
        raise ValueError(f"invalid ss58 address {address!r}")
    return data[-32:].hex()


def _error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _allowed(call: Any) -> bool:
    return isinstance(call, dict) and isinstance(call.get("method"), str) and call["method"] in ALLOWED


def tunnel_allowed(call: Any, key_setup_open: bool) -> bool:
    if not isinstance(call, dict) or not isinstance(call.get("method"), str):
        return False
    method, params = call["method"], call.get("params")
    if method in TUNNEL_BLOCKED:
        return False
    if method in KEY_SETUP:
        return key_setup_open
    if method == "state_subscribeStorage":
        return isinstance(params, list) and bool(params) and bool(params[0])
    return True


def _id(call: Any) -> Any:
    return call.get("id") if isinstance(call, dict) else None


def check(payload: Any, allowed: Callable[[Any], bool] = _allowed, level: int = logging.DEBUG) -> dict | list | None:
    if isinstance(payload, list):
        if not payload:
            return _error(None, -32600, "Invalid request")
        if all(allowed(c) for c in payload):
            return None
        for c in payload:
            if not allowed(c):
                logger.log(level, "rpc-proxy: blocked %r", c.get("method") if isinstance(c, dict) else "<invalid>")
        return [_error(_id(c), -32601, "Method not allowed") for c in payload]
    if allowed(payload):
        return None
    logger.log(level, "rpc-proxy: blocked %r", payload.get("method") if isinstance(payload, dict) else "<invalid>")
    return _error(_id(payload), -32601, "Method not allowed")


def parse_only(text: str | bytes) -> tuple[Any, dict | None]:
    try:
        return json.loads(text), None
    except ValueError:
        return None, _error(None, -32700, "Parse error")


def parse(text: str | bytes) -> tuple[Any, dict | list | None]:
    payload, err = parse_only(text)
    return (payload, err) if err is not None else (payload, check(payload))


async def public_policy(app: web.Application, payload: Any) -> dict | list | None:
    return check(payload)


def _wants_key_setup(payload: Any) -> bool:
    calls = payload if isinstance(payload, list) else [payload]
    return any(isinstance(c, dict) and c.get("method") in KEY_SETUP for c in calls)


async def _upstream_call(app: web.Application, method: str, params: list) -> Any:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    async with app[SESSION].post(app[UPSTREAM_HTTP_KEY], json=body) as resp:
        reply = await resp.json(content_type=None)
    if "error" in reply:
        raise RuntimeError(f"{method}: {reply['error']}")
    return reply["result"]


async def key_setup_open(app: web.Application) -> bool:
    if os.environ.get("TUNNEL_KEY_SETUP", "").strip().lower() in ("true", "on", "yes", "1"):
        return True
    try:
        if (await _upstream_call(app, "system_health", []))["isSyncing"]:
            return False
        account = ss58_account(await _upstream_call(app, "authorExt_getValidatorPublicKey", []))
        start = None
        while True:
            keys = await _upstream_call(app, "state_getKeysPaged", [SESSION_NEXT_KEYS, 1000, start])
            if any(k.endswith(account) for k in keys):
                return False
            if len(keys) < 1000:
                return True
            start = keys[-1]
    except Exception:
        logger.exception("rpc-proxy: could not tell whether session keys are set; keeping key setup closed")
        return False


async def tunnel_policy(app: web.Application, payload: Any) -> dict | list | None:
    open_ = await key_setup_open(app) if _wants_key_setup(payload) else False
    if _wants_key_setup(payload) and not open_:
        logger.info("rpc-proxy: key setup is closed once session keys are set on chain; set TUNNEL_KEY_SETUP=true to reopen")
    return check(payload, lambda c: tunnel_allowed(c, open_), logging.INFO)


async def _http(request: web.Request) -> web.Response:
    payload, err = parse_only(await request.read())
    if err is None:
        err = await request.app[POLICY](request.app, payload)
    if err is not None:
        return web.json_response(err)
    session: aiohttp.ClientSession = request.app[SESSION]
    try:
        async with session.post(request.app[UPSTREAM_HTTP_KEY], data=json.dumps(payload),
                                headers={"Content-Type": "application/json"}) as resp:
            body = await resp.read()
            return web.Response(body=body, status=resp.status, content_type="application/json")
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return web.json_response(_error(_id(payload), -32603, "Upstream unavailable"), status=502)


async def _ws(request: web.Request) -> web.WebSocketResponse:
    slots = request.app[WS_SLOTS]
    if slots.locked():
        raise web.HTTPServiceUnavailable()
    async with slots:
        return await _ws_session(request)


async def _ws_session(request: web.Request) -> web.WebSocketResponse:
    client = web.WebSocketResponse(max_msg_size=MAX_SIZE)
    await client.prepare(request)
    session: aiohttp.ClientSession = request.app[SESSION]
    try:
        upstream = await session.ws_connect(request.app[UPSTREAM_WS_KEY], max_msg_size=MAX_SIZE)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        await client.close(code=1011)
        return client

    async def client_to_upstream() -> None:
        async for msg in client:
            if msg.type != aiohttp.WSMsgType.TEXT:
                break
            payload, err = parse_only(msg.data)
            if err is None:
                err = await request.app[POLICY](request.app, payload)
            if err is not None:
                await client.send_str(json.dumps(err))
                continue
            await upstream.send_str(json.dumps(payload))

    async def upstream_to_client() -> None:
        async for msg in upstream:
            if msg.type != aiohttp.WSMsgType.TEXT:
                break
            await client.send_str(msg.data)

    tasks = [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())]
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in tasks:
        t.cancel()
    await upstream.close()
    await client.close()
    return client


async def _get(request: web.Request) -> web.StreamResponse:
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return await _ws(request)
    raise web.HTTPMethodNotAllowed("GET", ["POST"])


async def _session_ctx(app: web.Application):
    app[WS_SLOTS] = asyncio.Semaphore(MAX_WS)
    app[SESSION] = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60),
                                         connector=aiohttp.TCPConnector(limit=MAX_UPSTREAM))
    yield
    await app[SESSION].close()


def build_app(upstream_http: str = UPSTREAM_HTTP, upstream_ws: str = UPSTREAM_WS,
              policy: Callable[[web.Application, Any], Awaitable[dict | list | None]] = public_policy) -> web.Application:
    app = web.Application(client_max_size=MAX_SIZE)
    app[POLICY] = policy
    app[UPSTREAM_HTTP_KEY] = upstream_http
    app[UPSTREAM_WS_KEY] = upstream_ws
    app.cleanup_ctx.append(_session_ctx)
    app.router.add_post("/{tail:.*}", _http)
    app.router.add_get("/{tail:.*}", _get)
    return app


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


def fix_request_line(line: bytes) -> bytes:
    method, _, rest = line.partition(b" ")
    target, _, version = rest.partition(b" ")
    if version and not target.startswith((b"/", b"*")):
        return method + b" / " + version
    return line


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, port: int) -> None:
    try:
        line = await reader.readuntil(b"\r\n")
        up_reader, up_writer = await asyncio.open_connection("127.0.0.1", port)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, OSError):
        writer.close()
        return
    up_writer.write(fix_request_line(line))
    await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))


async def serve_tunnel(port: int = TUNNEL_PORT, app_port: int = TUNNEL_APP_PORT,
                       upstream_http: str = UPSTREAM_HTTP, upstream_ws: str = UPSTREAM_WS) -> None:
    runner = web.AppRunner(build_app(upstream_http, upstream_ws, policy=tunnel_policy))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", app_port).start()
    server = await asyncio.start_server(lambda r, w: _relay(r, w, app_port), "127.0.0.1", port)
    async with server:
        await server.serve_forever()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if sys.argv[1:] == ["tunnel"]:
        logger.info("rpc-proxy: tunnel filter on 127.0.0.1:%d, forwarding to %s", TUNNEL_PORT, UPSTREAM_HTTP)
        asyncio.run(serve_tunnel())
        return
    port = int(os.environ.get("RPC_PROXY_PORT") or "9945")
    logger.info("rpc-proxy: listening on 0.0.0.0:%d, forwarding allowlisted methods to %s", port, UPSTREAM_HTTP)
    web.run_app(build_app(), host="0.0.0.0", port=port, print=None)


if __name__ == "__main__":
    main()
