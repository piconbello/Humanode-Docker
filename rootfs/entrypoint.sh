#!/bin/sh
set -eu

ensure_data_layout() {
    if [ ! -d /data ]; then
        mkdir -p /data
        chmod 0755 /data
    fi
    mkdir -p /data/chains
    chown hmnd:hmnd /data/chains
    chmod 0750 /data/chains
    mkdir -p /data/bot-state
    chown botuser:botuser /data/bot-state
    chmod 0700 /data/bot-state
}

ensure_data_layout

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
