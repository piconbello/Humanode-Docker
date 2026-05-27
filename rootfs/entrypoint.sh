#!/bin/sh
set -eu

ensure_data_layout() {
    if [ ! -d /data ]; then
        mkdir -p /data
        chmod 0755 /data
    fi
    mkdir -p /data/chains
    chown -R hmnd:hmnd /data/chains
    chmod 0750 /data/chains
    mkdir -p /data/bot-state
    chown botuser:botuser /data/bot-state
    chmod 0700 /data/bot-state
}

ensure_data_layout

WORDS="amber atlas beacon bloom brass cedar chill cliff coral crane dusk echo ember fern flare forge frost gale grove haze iron jade knot lark leaf lunar maple mesa mist nova onyx orbit pearl plum prism pulse quartz ridge rune sage shore slate spark spire steel storm surge thorn tide timber torch vale viper wave wing zenith"

if [ -n "${NODE_NAME:-}" ]; then
    NODE_NAME="HND-${NODE_NAME}"
elif [ -f /data/.node-name ]; then
    NODE_NAME="$(cat /data/.node-name)"
else
    WORD=$(echo $WORDS | tr ' ' '\n' | shuf -n1)
    HEX=$(head -c3 /dev/urandom | od -An -tx1 | tr -d ' \n')
    NODE_NAME="HND-${WORD}-${HEX}"
    printf '%s' "$NODE_NAME" > /data/.node-name
fi
export NODE_NAME
echo "node-name: ${NODE_NAME}"

case "${1:-}" in
    insert-key)
        shift
        exec /usr/local/bin/insert-key.sh "$@"
        ;;
    *)
        if [ -z "$(find /data/chains -type f -path '*/keystore/*' 2>/dev/null | head -n 1)" ]; then
            echo "info: no keystore found; booting as non-validator. Run 'insert-key' to enable validator mode." >&2
        fi
        exec /init "$@"
        ;;
esac
