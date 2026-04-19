#!/bin/sh
set -eu

CHAINSPEC="${CHAINSPEC:-/etc/humanode/chainspec.json}"
BASE_PATH="${BASE_PATH:-/data}"

if [ $# -ne 0 ]; then
    echo "error: insert-key accepts seed via stdin only, no arguments" >&2
    echo "usage: echo \"\$SEED\" | docker run --rm -i -v <vol>:/data <image> insert-key" >&2
    exit 1
fi

if [ -t 0 ]; then
    echo "error: stdin is a TTY; pipe the seed in, do not type it interactively" >&2
    echo "usage: echo \"\$SEED\" | docker run --rm -i -v <vol>:/data <image> insert-key" >&2
    exit 1
fi

if find "$BASE_PATH/chains" -type f -path '*/keystore/6b626169*' 2>/dev/null | grep -q .; then
    echo "error: kbai keystore entry already exists under $BASE_PATH/chains/*/keystore/" >&2
    echo "to reinstall: tear down the container, 'docker volume rm' the data volume, then re-run insert-key." >&2
    exit 1
fi

SEED="$(cat)"

if [ -z "$SEED" ]; then
    echo "error: no seed received on stdin" >&2
    exit 1
fi

TMPSEED=""
for d in /dev/shm /run /tmp; do
    if [ -d "$d" ] && [ -w "$d" ]; then
        TMPSEED="$d/hmnd-seed.$$"
        break
    fi
done
if [ -z "$TMPSEED" ]; then
    echo "error: no writable tmpfs location for seed staging" >&2
    exit 1
fi

cleanup() {
    if [ -n "$TMPSEED" ] && [ -f "$TMPSEED" ]; then
        command -v shred > /dev/null 2>&1 && shred -u "$TMPSEED" 2>/dev/null || rm -f "$TMPSEED"
    fi
}
trap cleanup EXIT INT TERM

( umask 0377; printf '%s' "$SEED" > "$TMPSEED" )

humanode-peer key insert \
    --key-type kbai \
    --scheme sr25519 \
    --chain "$CHAINSPEC" \
    --base-path "$BASE_PATH" \
    --suri "$TMPSEED"

chown -R hmnd:hmnd "$BASE_PATH/chains"

find "$BASE_PATH/chains" -type d -name keystore 2>/dev/null | while IFS= read -r kdir; do
    chmod 0700 "$kdir"
    find "$kdir" -type f -exec chmod 0600 {} \;
done

echo "insert-key: keystore populated for key-type kbai"
