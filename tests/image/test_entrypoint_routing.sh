#!/bin/sh
# tests/image/test_entrypoint_routing.sh - Unit 2 entrypoint routing test.
#
# Verifies:
#   - `docker run <image> insert-key` routes to insert-key.sh, not s6-overlay
#   - default argv starts s6-overlay supervision tree
#   - /data layout is created with correct UIDs and perms on first boot
#
# Run from repo root:
#   sh tests/image/test_entrypoint_routing.sh

set -eu

IMAGE="${IMAGE:-hmnd-validator:test}"
VOL="hmnd-routing-$$"

cleanup() {
    docker volume rm "$VOL" > /dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

fail() {
    echo "FAIL: $1" >&2
    exit 1
}

docker volume create "$VOL" > /dev/null

echo "check: insert-key path bypasses s6"
# Pipe empty stdin so insert-key fails fast (we don't want to actually insert here).
# The key signal is that the error message comes from insert-key.sh, not from s6.
OUT="$(echo "" | docker run --rm -i -v "$VOL:/data" "$IMAGE" insert-key 2>&1 || true)"
echo "$OUT" | grep -q "no seed received on stdin" \
    || fail "insert-key argv did not route to insert-key.sh; got: $OUT"

echo "check: /data layout created after first entrypoint invocation"
docker run --rm -v "$VOL:/data" --entrypoint /entrypoint.sh "$IMAGE" insert-key < /dev/null 2>/dev/null || true
docker run --rm --entrypoint /bin/sh -v "$VOL:/data" "$IMAGE" -c \
    'stat -c "%a %U %G" /data/chains' | grep -q '^750 hmnd hmnd' \
    || fail "/data/chains perms wrong (expected 0750 hmnd:hmnd)"

docker run --rm --entrypoint /bin/sh -v "$VOL:/data" "$IMAGE" -c \
    'stat -c "%a %U %G" /data/bot-state' | grep -q '^700 botuser botuser' \
    || fail "/data/bot-state perms wrong (expected 0700 botuser:botuser)"

echo "check: botuser can restart the tunnel service but not the node"
CTR="hmnd-routing-ctr-$$"
docker run -d --rm --network none --name "$CTR" "$IMAGE" > /dev/null
for i in $(seq 1 30); do
    docker exec "$CTR" test -p /run/service/tunnel/supervise/control 2>/dev/null \
        && docker exec "$CTR" sh -c 'stat -c %G /run/service/tunnel/supervise/control' | grep -q '^botuser$' \
        && break
    sleep 1
done
docker exec -u botuser "$CTR" /command/s6-svc -r /run/service/tunnel \
    || { docker rm -f "$CTR" > /dev/null; fail "botuser cannot restart the tunnel service"; }
if docker exec -u botuser "$CTR" /command/s6-svc -r /run/service/node 2>/dev/null; then
    docker rm -f "$CTR" > /dev/null
    fail "botuser can control the node service"
fi
docker rm -f "$CTR" > /dev/null

echo "ok: entrypoint routing test passed"
