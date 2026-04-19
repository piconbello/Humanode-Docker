#!/bin/sh
set -eu

IMAGE="${IMAGE:-hmnd-validator:test}"
TEST_SEED="${TEST_SEED:-bottom drive obey lake curtain smoke basket hold race lonely fit walk}"
VOL="hmnd-seedlog-$$"
OUT_FILE="$(mktemp)"

cleanup() {
    docker volume rm "$VOL" > /dev/null 2>&1 || true
    rm -f "$OUT_FILE"
}
trap cleanup EXIT INT TERM

fail() {
    echo "FAIL: $1" >&2
    exit 1
}

docker volume create "$VOL" > /dev/null

echo "check: insert-key runs cleanly with test seed"
if ! echo "$TEST_SEED" | docker run --rm -i -v "$VOL:/data" "$IMAGE" insert-key > "$OUT_FILE" 2>&1; then
    echo "--- captured output ---"
    cat "$OUT_FILE"
    fail "insert-key exited non-zero"
fi

echo "check: no seed word appears in captured stdout+stderr"
LEAKED=""
for w in $TEST_SEED; do
    if grep -Fqw "$w" "$OUT_FILE"; then
        LEAKED="$LEAKED $w"
    fi
done

if [ -n "$LEAKED" ]; then
    echo "--- captured output ---"
    cat "$OUT_FILE"
    fail "seed word(s) found in insert-key output:$LEAKED"
fi

echo "check: the expected success line IS in the output (sanity: we captured something)"
grep -q "keystore populated for key-type kbai" "$OUT_FILE" \
    || fail "did not see the expected 'keystore populated' line; output capture may be broken"

echo "ok: seed is not echoed by insert-key or by humanode-peer"
