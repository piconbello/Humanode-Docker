#!/bin/sh
set -eu

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
    echo "telegram-notify: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set; skipping" >&2
    exit 0
fi

curl -fsS --retry 3 -o /dev/null \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=$1" \
    --data-urlencode "disable_web_page_preview=true" \
    || echo "telegram-notify: send failed" >&2
