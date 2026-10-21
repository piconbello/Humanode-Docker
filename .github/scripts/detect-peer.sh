#!/bin/sh
set -eu

PEER_FILE="${PEER_FILE:-artifacts/humanode-peer.env}"
MANIFEST_URL="${MANIFEST_URL:-https://chainspec.mainnet.stages.humanode.io/latest/manifest.json}"
PEER_REPO="${PEER_REPO:-humanode-network/humanode}"
PEER_ASSET="${PEER_ASSET:-humanode-peer-x86_64-unknown-linux-gnu-ubuntu2404.tar.gz}"
SEARCH_LIMIT="${SEARCH_LIMIT:-5}"
OUT="${GITHUB_OUTPUT:-/dev/stdout}"

. "$PEER_FILE"

manifest="$(curl -fsSL --retry 3 "$MANIFEST_URL")"
pick() {
    printf '%s' "$manifest" | jq -er --arg dest "$1" \
        '.binaries[] | select(.platform=="Linux" and .arch=="x86_64") | .files[] | select(.destinationSubPath==$dest) | .sha256'
}
peer_sha="$(pick humanode-peer)"
cs_sha="$(pick chainspec.json)"

if [ "$peer_sha" = "$PEER_SHA256" ] && [ "$cs_sha" = "$CHAINSPEC_SHA256" ]; then
    echo "detect-peer: pinned peer and chainspec match mainnet manifest" >&2
    echo "changed=false" >> "$OUT"
    exit 0
fi

tag="$PEER_TAG"
commit="$PEER_COMMIT"
if [ "$peer_sha" != "$PEER_SHA256" ]; then
    echo "detect-peer: manifest peer $peer_sha differs from pinned $PEER_SHA256" >&2
    WORK="$(mktemp -d)"
    trap 'rm -rf "$WORK"' EXIT INT TERM
    tag=""
    for t in $(gh release list --repo "$PEER_REPO" --limit "$SEARCH_LIMIT" --json tagName --jq '.[].tagName'); do
        echo "detect-peer: checking $t" >&2
        rm -rf "$WORK/x" && mkdir -p "$WORK/x"
        gh release download "$t" --repo "$PEER_REPO" -p "$PEER_ASSET" -O - | tar -C "$WORK/x" -xz || continue
        if [ "$(sha256sum "$WORK/x/humanode-peer" | cut -d' ' -f1)" = "$peer_sha" ]; then
            tag="$t"
            break
        fi
    done
    if [ -z "$tag" ]; then
        echo "detect-peer: no $PEER_REPO release among the last $SEARCH_LIMIT ships peer $peer_sha" >&2
        exit 1
    fi
    commit="$(gh api "repos/$PEER_REPO/commits/$tag" --jq .sha)"
fi

cat > "$PEER_FILE" <<PIN
PEER_TAG=$tag
PEER_COMMIT=$commit
PEER_SHA256=$peer_sha
CHAINSPEC_SHA256=$cs_sha
PIN

short="$(printf '%s' "$commit" | cut -c1-7)"
echo "detect-peer: $PEER_TAG ($PEER_COMMIT) -> $tag ($commit)" >&2
{
    echo "changed=true"
    echo "old_tag=$PEER_TAG"
    echo "old_commit=$PEER_COMMIT"
    echo "tag=$tag"
    echo "commit=$commit"
    echo "short=$short"
    echo "peer_sha=$peer_sha"
    echo "chainspec_sha=$cs_sha"
} >> "$OUT"
