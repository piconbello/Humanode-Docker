#!/bin/sh
set -eu

IMAGE="${IMAGE:-hmnd-validator:test}"
PEER_MIN="${PEER_MIN:-4}"
PEER_BUDGET_S="${PEER_BUDGET_S:-180}"
PROGRESS_BUDGET_S="${PROGRESS_BUDGET_S:-480}"
BLOCK_ADVANCE="${BLOCK_ADVANCE:-10}"
SYNC_MODE="${SYNC_MODE:-full}"

CONTAINER="hmnd-synctest-$$"
VOLUME="hmnd-synctest-vol-$$"

cleanup() {
    docker rm -f "$CONTAINER" > /dev/null 2>&1 || true
    docker volume rm "$VOLUME" > /dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

fail() {
    echo "FAIL: $1" >&2
    echo "--- last 50 log lines ---" >&2
    docker logs "$CONTAINER" 2>&1 | grep -v "runtime not yet wired" | tail -50 >&2 || true
    exit 1
}

rpc() {
    method="$1"; params="${2:-[]}"
    docker exec "$CONTAINER" sh -c "curl -sS --fail -H 'Content-Type: application/json' -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"$method\",\"params\":$params}' http://127.0.0.1:9944" 2>/dev/null
}

get_peers() {
    rpc system_health | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["peers"])' 2>/dev/null || echo 0
}

get_is_syncing() {
    rpc system_health | python3 -c 'import sys,json; print("true" if json.load(sys.stdin)["result"]["isSyncing"] else "false")' 2>/dev/null || echo "true"
}

get_best() {
    rpc chain_getHeader | python3 -c 'import sys,json; print(int(json.load(sys.stdin)["result"]["number"],16))' 2>/dev/null || echo 0
}

echo "sync-test: starting container from $IMAGE (SYNC_MODE=$SYNC_MODE)"
docker volume create "$VOLUME" > /dev/null
docker run -d --name "$CONTAINER" -v "$VOLUME:/data" -e "SYNC_MODE=$SYNC_MODE" "$IMAGE" > /dev/null

echo "sync-test: waiting for RPC to be reachable"
START="$(date +%s)"
until docker exec "$CONTAINER" sh -c 'curl -sS -o /dev/null -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"system_health\",\"params\":[]}" http://127.0.0.1:9944' > /dev/null 2>&1; do
    NOW="$(date +%s)"
    if [ $((NOW - START)) -gt 60 ]; then
        fail "RPC did not come up on 127.0.0.1:9944 within 60s"
    fi
    sleep 2
done

STATUS="$(docker inspect -f '{{.State.Status}}' "$CONTAINER")"
[ "$STATUS" = "running" ] || fail "container status is $STATUS, expected running"

echo "sync-test: waiting for peers >= $PEER_MIN (budget ${PEER_BUDGET_S}s)"
START="$(date +%s)"
while :; do
    PEERS="$(get_peers)"
    echo "sync-test: peers=$PEERS"
    [ "$PEERS" -ge "$PEER_MIN" ] && break
    NOW="$(date +%s)"
    if [ $((NOW - START)) -gt "$PEER_BUDGET_S" ]; then
        fail "peer count stayed below $PEER_MIN for ${PEER_BUDGET_S}s (last=$PEERS)"
    fi
    sleep 5
done
echo "sync-test: reached $PEERS peers in $(($(date +%s) - START))s"

echo "sync-test: waiting for best block to advance by $BLOCK_ADVANCE (budget ${PROGRESS_BUDGET_S}s)"
START="$(date +%s)"
BASELINE_BEST="$(get_best)"
echo "sync-test: baseline best=$BASELINE_BEST"
while :; do
    BEST="$(get_best)"
    if [ "$((BEST - BASELINE_BEST))" -ge "$BLOCK_ADVANCE" ]; then
        echo "sync-test: best block advanced $BASELINE_BEST -> $BEST (+$((BEST - BASELINE_BEST)))"
        break
    fi
    NOW="$(date +%s)"
    ELAPSED=$((NOW - START))
    if [ $((ELAPSED % 30)) -lt 5 ]; then
        echo "sync-test: elapsed=${ELAPSED}s best=$BEST peers=$(get_peers)"
    fi
    if [ "$ELAPSED" -gt "$PROGRESS_BUDGET_S" ]; then
        fail "best block advanced less than $BLOCK_ADVANCE in ${PROGRESS_BUDGET_S}s (best=$BEST, baseline=$BASELINE_BEST)"
    fi
    sleep 5
done

echo "sync-test: asserting node log markers visible in docker logs"
LOGS="$(docker logs "$CONTAINER" 2>&1)"
for marker in "Running JSON-RPC server" "Highest known block"; do
    echo "$LOGS" | grep -q "$marker" || fail "expected node log marker missing from docker logs: '$marker'"
done
echo "sync-test: docker logs contains expected node markers"

STATUS="$(docker inspect -f '{{.State.Status}}' "$CONTAINER")"
[ "$STATUS" = "running" ] || fail "container exited during sync-test (status=$STATUS)"

echo "sync-test: OK"
