#!/bin/sh
set -eu

VERSION_FILE="${VERSION_FILE:-/build/humanode-version.txt}"
OUT_BIN="${OUT_BIN:-/usr/local/bin/humanode-peer}"
OUT_NGROK="${OUT_NGROK:-/usr/local/bin/ngrok}"
OUT_CHAINSPEC="${OUT_CHAINSPEC:-/etc/humanode/chainspec.json}"
PACKAGE_NAME="${PACKAGE_NAME:-Humanode Mainnet}"

if [ ! -f "$VERSION_FILE" ]; then
    echo "fetch-upstream: version file not found at $VERSION_FILE" >&2
    exit 1
fi

VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"
if [ -z "$VERSION" ]; then
    echo "fetch-upstream: version file is empty" >&2
    exit 1
fi

REPO="humanode-network/distribution"
ASSET="humanode-distribution-x86_64-unknown-linux-gnu.tar.gz"
TARBALL_URL="https://github.com/${REPO}/releases/download/${VERSION}/${ASSET}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT INT TERM

echo "fetch-upstream: version=$VERSION"
echo "fetch-upstream: downloading installer from $TARBALL_URL"
curl -fsSL --retry 3 "$TARBALL_URL" -o "$WORK/hd.tgz"
tar -C "$WORK" -xzf "$WORK/hd.tgz"

INSTALLER="$WORK/humanode-distribution"
if [ ! -x "$INSTALLER" ]; then
    echo "fetch-upstream: installer binary not found inside tarball" >&2
    exit 1
fi

INSTALL_DIR="$WORK/install"
mkdir -p "$INSTALL_DIR"

echo "fetch-upstream: installing package '$PACKAGE_NAME' into $INSTALL_DIR"
"$INSTALLER" install --package-display-name "$PACKAGE_NAME" -d "$INSTALL_DIR"

mkdir -p "$(dirname "$OUT_BIN")" "$(dirname "$OUT_CHAINSPEC")"

for f in humanode-peer chainspec.json ngrok; do
    if [ ! -e "$INSTALL_DIR/$f" ]; then
        echo "fetch-upstream: expected file missing from install: $f" >&2
        exit 1
    fi
done

install -m 0755 "$INSTALL_DIR/humanode-peer" "$OUT_BIN"
install -m 0755 "$INSTALL_DIR/ngrok" "$OUT_NGROK"
install -m 0644 "$INSTALL_DIR/chainspec.json" "$OUT_CHAINSPEC"

"$OUT_BIN" --version > /dev/null 2>&1 || {
    echo "fetch-upstream: humanode-peer --version failed" >&2
    exit 1
}

python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$OUT_CHAINSPEC" || {
    echo "fetch-upstream: chainspec is not valid JSON" >&2
    exit 1
}

echo "fetch-upstream: done"
