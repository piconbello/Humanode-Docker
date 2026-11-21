from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

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


def _error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _allowed(call: Any) -> bool:
    return isinstance(call, dict) and isinstance(call.get("method"), str) and call["method"] in ALLOWED


def _id(call: Any) -> Any:
    return call.get("id") if isinstance(call, dict) else None


def check(payload: Any) -> dict | list | None:
    if isinstance(payload, list):
        if not payload:
            return _error(None, -32600, "Invalid request")
        if all(_allowed(c) for c in payload):
            return None
        for c in payload:
            if not _allowed(c):
                logger.debug("rpc-proxy: blocked %r", c.get("method") if isinstance(c, dict) else "<invalid>")
        return [_error(_id(c), -32601, "Method not allowed") for c in payload]
    if _allowed(payload):
        return None
    logger.debug("rpc-proxy: blocked %r", payload.get("method") if isinstance(payload, dict) else "<invalid>")
    return _error(_id(payload), -32601, "Method not allowed")


def parse(text: str | bytes) -> tuple[Any, dict | list | None]:
    try:
        payload = json.loads(text)
    except ValueError:
        return None, _error(None, -32700, "Parse error")
    return payload, check(payload)


async def _http(request: web.Request) -> web.Response:
    payload, err = parse(await request.read())
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
            payload, err = parse(msg.data)
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


def build_app(upstream_http: str = UPSTREAM_HTTP, upstream_ws: str = UPSTREAM_WS) -> web.Application:
    app = web.Application(client_max_size=MAX_SIZE)
    app[UPSTREAM_HTTP_KEY] = upstream_http
    app[UPSTREAM_WS_KEY] = upstream_ws
    app.cleanup_ctx.append(_session_ctx)
    app.router.add_post("/{tail:.*}", _http)
    app.router.add_get("/{tail:.*}", _get)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    port = int(os.environ.get("RPC_PROXY_PORT") or "9945")
    logger.info("rpc-proxy: listening on 0.0.0.0:%d, forwarding allowlisted methods to %s", port, UPSTREAM_HTTP)
    web.run_app(build_app(), host="0.0.0.0", port=port, print=None)


if __name__ == "__main__":
    main()
