#!/bin/sh
set -eu

PEER_FILE="${PEER_FILE:-/build/humanode-peer.env}"
OUT_BIN="${OUT_BIN:-/usr/local/bin/humanode-peer}"
OUT_CHAINSPEC="${OUT_CHAINSPEC:-/etc/humanode/chainspec.json}"
PEER_REPO="${PEER_REPO:-humanode-network/humanode}"
PEER_ASSET="${PEER_ASSET:-humanode-peer-x86_64-unknown-linux-gnu-ubuntu2404.tar.gz}"
CHAINSPEC_URL="${CHAINSPEC_URL:-https://chainspec.mainnet.stages.humanode.io/latest/chainspec.json}"

if [ ! -f "$PEER_FILE" ]; then
    echo "fetch-upstream: peer pin file not found at $PEER_FILE" >&2
    exit 1
fi

. "$PEER_FILE"

for v in PEER_TAG PEER_COMMIT PEER_SHA256 CHAINSPEC_SHA256; do
    eval "val=\${$v:-}"
    if [ -z "$val" ]; then
        echo "fetch-upstream: $v missing from $PEER_FILE" >&2
        exit 1
    fi
done

verify() {
    actual="$(sha256sum "$1" | cut -d' ' -f1)"
    if [ "$actual" != "$2" ]; then
        echo "fetch-upstream: sha256 mismatch for $3: expected $2, got $actual" >&2
        exit 1
    fi
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT INT TERM

PEER_URL="https://github.com/${PEER_REPO}/releases/download/${PEER_TAG}/${PEER_ASSET}"
echo "fetch-upstream: peer=$PEER_TAG ($PEER_COMMIT)"
echo "fetch-upstream: downloading $PEER_URL"
curl -fsSL --retry 3 "$PEER_URL" -o "$WORK/peer.tgz"
tar -C "$WORK" -xzf "$WORK/peer.tgz"
if [ ! -f "$WORK/humanode-peer" ]; then
    echo "fetch-upstream: humanode-peer not found inside $PEER_ASSET" >&2
    exit 1
fi
verify "$WORK/humanode-peer" "$PEER_SHA256" humanode-peer

echo "fetch-upstream: downloading $CHAINSPEC_URL"
curl -fsSL --retry 3 "$CHAINSPEC_URL" -o "$WORK/chainspec.json"
verify "$WORK/chainspec.json" "$CHAINSPEC_SHA256" chainspec.json

mkdir -p "$(dirname "$OUT_BIN")" "$(dirname "$OUT_CHAINSPEC")"
install -m 0755 "$WORK/humanode-peer" "$OUT_BIN"
install -m 0644 "$WORK/chainspec.json" "$OUT_CHAINSPEC"
install -m 0644 "$PEER_FILE" "$(dirname "$OUT_CHAINSPEC")/peer.env"

"$OUT_BIN" --version > /dev/null 2>&1 || {
    echo "fetch-upstream: humanode-peer --version failed" >&2
    exit 1
}

python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$OUT_CHAINSPEC" || {
    echo "fetch-upstream: chainspec is not valid JSON" >&2
    exit 1
}

echo "fetch-upstream: done"
