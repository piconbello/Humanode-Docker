#!/bin/sh
set -eu

IMAGE="${IMAGE:-hmnd-validator:test}"
HOST_PORT="${HOST_PORT:-19945}"
C1="hmnd-rpc-off-$$"
C2="hmnd-rpc-on-$$"
C3="hmnd-rpc-tunnel-$$"
ZERO_HASH="0x0000000000000000000000000000000000000000000000000000000000000000"
LIVENESS='[{"faceScan":"","auditTrailImage":"","lowQualityAuditTrailImage":""}]'

cleanup() {
    docker rm -f "$C1" "$C2" "$C3" > /dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

fail() {
    echo "FAIL: $1" >&2
    exit 1
}

rpc() {
    curl -sS -m 10 -H 'Content-Type: application/json' -d "$2" "$1"
}

req() {
    printf '{"jsonrpc":"2.0","id":1,"method":"%s","params":%s}' "$1" "${2:-[]}"
}

wait_rpc() {
    i=0
    until docker exec "$1" curl -s -m 2 -o /dev/null -H 'Content-Type: application/json' \
        -d "$(req system_health)" "http://127.0.0.1:$2" 2>/dev/null; do
        i=$((i + 1))
        [ "$i" -lt 60 ] || fail "$1: RPC on $2 not up"
        sleep 1
    done
}

wait_log() {
    i=0
    until docker logs "$1" 2>&1 | grep -q "$2"; do
        i=$((i + 1))
        [ "$i" -lt 30 ] || fail "$1: no log line '$2'"
        sleep 1
    done
}

peer_args() {
    docker exec "$1" sh -c 'ps -o args= -C humanode-peer'
}

echo "check: defaults keep node args unchanged and proxy off"
docker run -d --name "$C1" "$IMAGE" > /dev/null
wait_rpc "$C1" 9944
args=$(peer_args "$C1")
echo "$args" | grep -q -- '--db-cache 256$' || fail "default args changed: $args"
if docker exec "$C1" curl -s -m 2 -o /dev/null "http://127.0.0.1:9945" 2>/dev/null; then
    fail "proxy listening while RPC_PROXY unset"
fi
docker logs "$C1" 2>&1 | grep -q 'rpc-proxy: disabled' || fail "no rpc-proxy disabled notice"
docker logs "$C1" 2>&1 | grep -q 'tunnel-proxy: disabled' || fail "tunnel filter running without VALIDATOR"
docker rm -f "$C1" > /dev/null

echo "check: proxy on, extra args passed through"
docker run -d --name "$C2" -e RPC_PROXY=true -e NODE_EXTRA_ARGS="--rpc-max-connections 50" \
    -p "127.0.0.1:$HOST_PORT:9945" "$IMAGE" > /dev/null
wait_rpc "$C2" 9944
wait_rpc "$C2" 9945
peer_args "$C2" | grep -q -- '--rpc-max-connections 50' || fail "NODE_EXTRA_ARGS not passed through"
peer_args "$C2" | grep -q -- '--rpc-external' && fail "node RPC must stay on loopback"
docker exec "$C2" sh -c 'ps -o user=,args= -C python3' | grep 'rpc_proxy' | grep -q '^nobody' \
    || fail "proxy not running as nobody"

URL="http://127.0.0.1:$HOST_PORT"

echo "check: every allowlisted method exists on this humanode-peer"
docker exec -i "$C2" python3 - <<'EOF' || fail "allowlist names a method the node does not serve"
import json, urllib.request
from hmnd_bot.rpc_proxy import ALLOWED
req = urllib.request.Request("http://127.0.0.1:9944", data=b'{"jsonrpc":"2.0","id":1,"method":"rpc_methods","params":[]}',
                             headers={"Content-Type": "application/json"})
missing = ALLOWED - set(json.load(urllib.request.urlopen(req))["result"]["methods"])
assert not missing, sorted(missing)
EOF

echo "check: allowed methods work through the published port"
rpc "$URL" "$(req eth_chainId)" | grep -q '"result":"0x' || fail "eth_chainId not served"
rpc "$URL" "$(req system_health)" | grep -q '"result":{' || fail "system_health not served"
rpc "$URL" "$(req state_getStorage '["0x00"]')" | grep -q '"result":null' || fail "state_getStorage not served"

echo "check: key, bioauth and admin methods are blocked"
for m in author_rotateKeys author_insertKey author_hasKey authorExt_setKeys bioauth_status \
         bioauth_enroll bioauth_authenticate system_addReservedPeer system_peers eth_sendTransaction; do
    rpc "$URL" "$(req "$m")" | grep -q 'Method not allowed' || fail "$m not blocked"
done
rpc "$URL" "$(req bioauth_enroll "$LIVENESS")" | grep -q 'Method not allowed' || fail "bioauth_enroll with params not blocked"
batch=$(rpc "$URL" "[$(req eth_chainId),$(req author_rotateKeys)]")
echo "$batch" | grep -q 'Method not allowed' || fail "mixed batch not blocked: $batch"
echo "$batch" | grep -q '"result"' && fail "mixed batch partially served: $batch"
rpc "$URL" "[$(req eth_chainId),$(req eth_blockNumber)]" | grep -q '"result":"0x' || fail "allowed batch not served"

echo "check: websocket filtering"
docker exec -i "$C2" python3 - <<'EOF' || fail "websocket filtering"
import asyncio, json, aiohttp

async def main():
    async with aiohttp.ClientSession() as s, s.ws_connect("ws://127.0.0.1:9945") as ws:
        await ws.send_str(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "author_rotateKeys", "params": []}))
        assert "Method not allowed" in (await ws.receive()).data
        await ws.send_str(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "chain_subscribeNewHeads", "params": []}))
        assert '"result"' in (await ws.receive()).data

asyncio.run(main())
EOF

echo "check: loopback node RPC still serves unsafe methods for the tunnel"
direct=$(docker exec "$C2" curl -sS -m 10 -H 'Content-Type: application/json' \
    -d "$(req bioauth_enroll "$LIVENESS")" http://127.0.0.1:9944)
echo "$direct" | grep -q 'unsafe' && fail "bioauth_enroll refused on loopback: $direct"
echo "$direct" | grep -q 'validatorKeyNotAvailable' || fail "unexpected bioauth_enroll reply: $direct"
docker exec "$C2" curl -sS -m 10 -H 'Content-Type: application/json' \
    -d "$(req state_traceBlock "[\"$ZERO_HASH\",null,null,null]")" http://127.0.0.1:9944 \
    | grep -q 'unsafe' && fail "loopback switched to safe mode"

echo "check: validator tunnel goes through the filter on 127.0.0.1:9946"
docker run -d --name "$C3" --network none -e VALIDATOR=true "$IMAGE" > /dev/null
wait_rpc "$C3" 9944
wait_rpc "$C3" 9946
docker exec "$C3" sh -c 'ps -o user=,args= -C python3' | grep 'rpc_proxy tunnel' | grep -q '^nobody' \
    || fail "tunnel filter not running as nobody"
wait_log "$C3" 'tunnel: RPC is up on 9946'
tun() { docker exec "$C3" curl -sS -m 10 -H 'Content-Type: application/json' -d "$1" http://127.0.0.1:9946; }
for m in author_insertKey author_hasKey system_addReservedPeer state_traceBlock offchain_localStorageSet author_rotateKeys authorExt_setKeys; do
    tun "$(req "$m")" | grep -q 'Method not allowed' || fail "$m not blocked on the tunnel"
done
tun "$(req bioauth_status)" | grep -q '"result"' || fail "bioauth_status blocked on the tunnel"
tun "$(req bioauth_enroll "$LIVENESS")" | grep -q 'validatorKeyNotAvailable' || fail "bioauth_enroll not forwarded on the tunnel"
docker exec "$C3" curl -sS -m 10 --request-target 'abcd.ws1.htunnel.app' -H 'Content-Type: application/json' \
    -d "$(req system_health)" http://127.0.0.1:9946 | grep -q '"result"' || fail "hostname request target rejected"
docker exec -i "$C3" python3 - <<'WSEOF' || fail "tunnel websocket path"
import asyncio, json, aiohttp

def req(i, m, p=None):
    return json.dumps({"jsonrpc": "2.0", "id": i, "method": m, "params": p or []})

async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("ws://127.0.0.1:9946", max_msg_size=0) as ws:
            await ws.send_str(req(1, "author_insertKey"))
            assert "Method not allowed" in (await ws.receive(timeout=10)).data
            await ws.send_str(req(2, "bioauth_status"))
            assert '"result"' in (await ws.receive(timeout=10)).data
            await ws.send_str(req(3, "chain_subscribeNewHeads"))
            assert '"result"' in (await ws.receive(timeout=10)).data
        big = req(4, "state_getStorage", ["0x" + "ab" * (5 * 1024 * 1024 // 2)])
        replies = []
        for port in (9946, 9944):
            async with s.ws_connect(f"ws://127.0.0.1:{port}", max_msg_size=0) as ws:
                await ws.send_str(big)
                replies.append((await ws.receive(timeout=30)).data)
        assert replies[0] == replies[1], replies
        for _ in range(3):
            conns = []
            for _ in range(10):
                r, w = await asyncio.open_connection("127.0.0.1", 9946)
                w.write(b"GET / HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n")
                assert (await r.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 101")
                conns.append(w)
            for w in conns:
                w.transport.abort()
            await asyncio.sleep(2)
        async with s.ws_connect("ws://127.0.0.1:9946") as ws:
            await ws.send_str(req(5, "system_health"))
            assert '"result"' in (await ws.receive(timeout=10)).data

asyncio.run(main())
WSEOF
docker rm -f "$C3" > /dev/null

echo "check: TUNNEL_FILTER=false points the tunnel at the node directly"
docker run -d --name "$C3" --network none -e VALIDATOR=true -e TUNNEL_FILTER=false "$IMAGE" > /dev/null
wait_rpc "$C3" 9944
wait_log "$C3" 'tunnel: RPC is up on 9944'
docker logs "$C3" 2>&1 | grep -q 'tunnel-proxy: disabled' || fail "tunnel filter still running"

echo "PASS: rpc proxy"
