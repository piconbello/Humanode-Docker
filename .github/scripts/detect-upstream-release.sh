#!/bin/sh
set -eu

REPO="${GH_REPO:-humanode-network/distribution}"

if command -v gh > /dev/null 2>&1; then
    tag="$(gh release list --repo "$REPO" --limit 1 --json tagName --jq '.[0].tagName')"
    if [ -n "$tag" ]; then
        printf '%s\n' "$tag"
        exit 0
    fi
fi

tag="$(curl -sSfL "https://github.com/$REPO/releases" \
        | grep -oE 'releases/tag/[^"]+' \
        | head -1 \
        | cut -d/ -f3)"
if [ -z "$tag" ]; then
    echo "detect-upstream-release: could not determine latest tag" >&2
    exit 1
fi
printf '%s\n' "$tag"
