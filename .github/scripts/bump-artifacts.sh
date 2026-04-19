#!/bin/sh
set -eu

if [ $# -ne 1 ] || [ -z "$1" ]; then
    echo "usage: bump-artifacts.sh <new-version>" >&2
    exit 2
fi

NEW="$1"
VERSION_FILE="artifacts/humanode-version.txt"

CURRENT="$(tr -d '[:space:]' < "$VERSION_FILE")"
if [ "$CURRENT" = "$NEW" ]; then
    echo "bump-artifacts: already at $NEW; nothing to do"
    exit 0
fi

printf '%s\n' "$NEW" > "$VERSION_FILE"
echo "bump-artifacts: $CURRENT -> $NEW"
