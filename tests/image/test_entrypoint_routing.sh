#!/bin/sh
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

echo "ok: entrypoint routing test passed"
