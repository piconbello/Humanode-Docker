import json

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from hmnd_bot.rpc_proxy import ALLOWED, build_app, check

NODE_METHODS = set("""
account_nextIndex authorExt_getValidatorPublicKey authorExt_setKeys author_hasKey author_hasSessionKeys
author_insertKey author_pendingExtrinsics author_removeExtrinsic author_rotateKeys author_submitAndWatchExtrinsic
author_submitExtrinsic author_unwatchExtrinsic babe_epochAuthorship bioauth_authenticate bioauth_authenticateStepV1
bioauth_authenticateV2 bioauth_enroll bioauth_enrollStepV1 bioauth_enrollV2 bioauth_getFacetecDeviceSdkParams
bioauth_getFacetecDeviceSdkParamsV2 bioauth_getFacetecSessionToken bioauth_status chainHead_unstable_body
chainHead_unstable_call chainHead_unstable_follow chainHead_unstable_genesisHash chainHead_unstable_header
chainHead_unstable_stopBody chainHead_unstable_stopCall chainHead_unstable_stopStorage chainHead_unstable_storage
chainHead_unstable_unfollow chainHead_unstable_unpin chainSpec_unstable_chainName chainSpec_unstable_genesisHash
chainSpec_unstable_properties chain_getBlock chain_getBlockHash chain_getFinalisedHead chain_getFinalizedHead
chain_getHead chain_getHeader chain_getRuntimeVersion chain_subscribeAllHeads chain_subscribeFinalisedHeads
chain_subscribeFinalizedHeads chain_subscribeNewHead chain_subscribeNewHeads chain_subscribeRuntimeVersion
chain_unsubscribeAllHeads chain_unsubscribeFinalisedHeads chain_unsubscribeFinalizedHeads chain_unsubscribeNewHead
chain_unsubscribeNewHeads chain_unsubscribeRuntimeVersion childstate_getKeys childstate_getKeysPaged
childstate_getKeysPagedAt childstate_getStorage childstate_getStorageEntries childstate_getStorageHash
childstate_getStorageSize eth_accounts eth_blockNumber eth_call eth_chainId eth_coinbase eth_estimateGas
eth_feeHistory eth_gasPrice eth_getBalance eth_getBlockByHash eth_getBlockByNumber eth_getBlockTransactionCountByHash
eth_getBlockTransactionCountByNumber eth_getCode eth_getFilterChanges eth_getFilterLogs eth_getLogs eth_getStorageAt
eth_getTransactionByBlockHashAndIndex eth_getTransactionByBlockNumberAndIndex eth_getTransactionByHash
eth_getTransactionCount eth_getTransactionReceipt eth_getUncleByBlockHashAndIndex eth_getUncleByBlockNumberAndIndex
eth_getUncleCountByBlockHash eth_getUncleCountByBlockNumber eth_getWork eth_hashrate eth_maxPriorityFeePerGas
eth_mining eth_newBlockFilter eth_newFilter eth_newPendingTransactionFilter eth_protocolVersion eth_sendRawTransaction
eth_sendTransaction eth_submitHashrate eth_submitWork eth_subscribe eth_syncing eth_uninstallFilter eth_unsubscribe
grandpa_proveFinality grandpa_roundState grandpa_subscribeJustifications grandpa_unsubscribeJustifications
net_listening net_peerCount net_version offchain_localStorageGet offchain_localStorageSet payment_queryFeeDetails
payment_queryInfo state_call state_callAt state_getChildReadProof state_getKeys state_getKeysPaged
state_getKeysPagedAt state_getMetadata state_getPairs state_getReadProof state_getRuntimeVersion state_getStorage
state_getStorageAt state_getStorageHash state_getStorageHashAt state_getStorageSize state_getStorageSizeAt
state_queryStorage state_queryStorageAt state_subscribeRuntimeVersion state_subscribeStorage state_traceBlock
state_unsubscribeRuntimeVersion state_unsubscribeStorage subscribe_newHead system_accountNextIndex
system_addLogFilter system_addReservedPeer system_chain system_chainType system_dryRun system_dryRunAt system_health
system_localListenAddresses system_localPeerId system_name system_nodeRoles system_peers system_properties
system_removeReservedPeer system_reservedPeers system_resetLogFilter system_syncState system_unstable_networkState
system_version transaction_unstable_submitAndWatch transaction_unstable_unwatch txpool_content txpool_inspect
txpool_status unsubscribe_newHead web3_clientVersion web3_sha3
""".split())

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


def test_allowlist_is_known_and_excludes_unsafe_and_validator_methods():
    assert len(NODE_METHODS) == 169
    assert ALLOWED <= NODE_METHODS
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
