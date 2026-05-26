#!/bin/sh
# tests/image/test_insert_key.sh - Unit 2 insert-key subcommand test.
#
# Verifies:
#   - insert-key with no stdin (TTY or empty) fails
#   - insert-key with argv arguments beyond the subcommand fails
#   - insert-key with a seed on stdin populates /data/chains/*/keystore with 0700 dir
#   - a second insert-key against the same volume refuses
#   - runtime start against an empty volume logs a non-validator notice
#
# Uses a throwaway named volume per run, wiped on completion.
#
# Run from repo root:
#   sh tests/image/test_insert_key.sh
#
# Env:
#   IMAGE          image tag to test (default: hmnd-validator:test)
#   TEST_SEED      seed mnemonic to use (default: a well-known test mnemonic)

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
# We simulate TTY by allocating one with -t and not piping stdin.
if docker run --rm -it -v "$VOL:/data" "$IMAGE" insert-key < /dev/null 2>/dev/null; then
    fail "insert-key accepted TTY stdin"
fi

echo "check: insert-key populates keystore"
echo "$TEST_SEED" | docker run --rm -i -v "$VOL:/data" "$IMAGE" insert-key \
    || fail "insert-key failed on valid stdin seed"

# Verify keystore exists and has correct perms.
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

echo "check: runtime start on empty volume logs non-validator notice"
VOL2="hmnd-test-empty-$$"
docker volume create "$VOL2" > /dev/null
OUT="$(timeout 10 docker run --rm -v "$VOL2:/data" "$IMAGE" 2>&1 || true)"
docker volume rm "$VOL2" > /dev/null 2>&1 || true
echo "$OUT" | grep -q "no keystore found" \
    || fail "empty-volume boot should log 'no keystore found'; got: $OUT"

echo "ok: insert-key test suite passed"
