#!/bin/sh
set -eu

IMAGE="${IMAGE:-hmnd-validator:test}"
TEST_SEED="${TEST_SEED:-bottom drive obey lake curtain smoke basket hold race lonely fit walk}"
VOL="hmnd-test-$$"

cleanup() {
    docker volume rm "$VOL" > /dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

fail() {
    echo "FAIL: $1" >&2
    exit 1
}

docker volume create "$VOL" > /dev/null

echo "check: insert-key with argv is rejected"
if echo "$TEST_SEED" | docker run --rm -i -v "$VOL:/data" "$IMAGE" insert-key --key-type kbai 2>/dev/null; then
    fail "insert-key accepted argv; should have refused"
fi

echo "check: insert-key with empty stdin is rejected"
if echo "" | docker run --rm -i -v "$VOL:/data" "$IMAGE" insert-key 2>/dev/null; then
    fail "insert-key accepted empty stdin"
fi

echo "check: insert-key with TTY stdin is rejected"
if docker run --rm -it -v "$VOL:/data" "$IMAGE" insert-key < /dev/null 2>/dev/null; then
    fail "insert-key accepted TTY stdin"
fi

echo "check: insert-key populates keystore"
echo "$TEST_SEED" | docker run --rm -i -v "$VOL:/data" "$IMAGE" insert-key \
    || fail "insert-key failed on valid stdin seed"

docker run --rm --entrypoint /bin/sh -v "$VOL:/data" "$IMAGE" -c \
    'find /data/chains -type d -name keystore | head -n 1 | xargs -I{} stat -c "%a %U" {}' \
    | grep -q '^700 hmnd' \
    || fail "keystore directory is not 0700 hmnd"

docker run --rm --entrypoint /bin/sh -v "$VOL:/data" "$IMAGE" -c \
    'find /data/chains -type f -path "*/keystore/6b626169*" | grep -q .' \
    || fail "no kbai keystore file found"

echo "check: second insert-key against populated volume refuses"
if echo "$TEST_SEED" | docker run --rm -i -v "$VOL:/data" "$IMAGE" insert-key 2>/dev/null; then
    fail "second insert-key accepted; should have refused"
fi

echo "check: node can create rocksdb dir on volume populated by insert-key"
VOL_RW="hmnd-test-rw-$$"
docker volume create "$VOL_RW" > /dev/null
echo "$TEST_SEED" | docker run --rm -i -v "$VOL_RW:/data" "$IMAGE" insert-key > /dev/null \
    || { docker volume rm "$VOL_RW" > /dev/null 2>&1; fail "setup insert-key failed"; }
if ! docker run --rm --entrypoint /bin/sh -v "$VOL_RW:/data" "$IMAGE" -c \
        '/command/s6-setuidgid hmnd mkdir -p /data/chains/humanode_mainnet/db/full' 2>/dev/null; then
    docker volume rm "$VOL_RW" > /dev/null 2>&1
    fail "hmnd cannot mkdir under /data/chains/<chain>/ after insert-key (root ownership regression)"
fi
docker volume rm "$VOL_RW" > /dev/null 2>&1

echo "check: runtime start on empty volume logs non-validator notice and proceeds"
VOL2="hmnd-test-empty-$$"
docker volume create "$VOL2" > /dev/null
OUT="$(timeout 3 docker run --rm -v "$VOL2:/data" "$IMAGE" 2>&1 || true)"
docker volume rm "$VOL2" > /dev/null 2>&1 || true
echo "$OUT" | grep -q "booting as non-validator" \
    || fail "empty-volume boot should log 'booting as non-validator'; got: $OUT"

echo "ok: insert-key test suite passed"
