#!/bin/sh
set -eu

IMAGE="${IMAGE:-hmnd-validator:test}"
DOCKERFILE_DIR="${DOCKERFILE_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

echo "build: building $IMAGE from $DOCKERFILE_DIR"
docker build -t "$IMAGE" "$DOCKERFILE_DIR" > /dev/null

fail() {
    echo "FAIL: $1" >&2
    exit 1
}

echo "check: users exist"
docker run --rm --entrypoint /bin/sh "$IMAGE" -c 'id hmnd && id botuser' > /dev/null \
    || fail "hmnd or botuser user missing"

docker run --rm --entrypoint /bin/sh "$IMAGE" -c 'test "$(id -u hmnd)" = "1100"' \
    || fail "hmnd UID is not 1100"

docker run --rm --entrypoint /bin/sh "$IMAGE" -c 'test "$(id -u botuser)" = "1101"' \
    || fail "botuser UID is not 1101"

echo "check: chainspec present at fixed path"
docker run --rm --entrypoint /bin/sh "$IMAGE" -c 'test -f /etc/humanode/chainspec.json' \
    || fail "chainspec missing at /etc/humanode/chainspec.json"

docker run --rm --entrypoint /bin/sh "$IMAGE" -c \
    'python3 -c "import json,sys; json.load(open(\"/etc/humanode/chainspec.json\"))"' \
    || fail "chainspec is not valid JSON"

echo "check: humanode-peer --version exits 0"
docker run --rm --entrypoint /bin/sh "$IMAGE" -c 'humanode-peer --version' > /dev/null \
    || fail "humanode-peer --version failed"

echo "check: only 30333/tcp is declared by the image"
EXPOSED="$(docker inspect "$IMAGE" --format '{{range $p, $_ := .Config.ExposedPorts}}{{$p}} {{end}}' | tr -d '[:space:]')"
if [ "$EXPOSED" != "30333/tcp" ]; then
    fail "unexpected exposed ports: $EXPOSED (expected 30333/tcp only)"
fi

echo "check: entrypoint is /entrypoint.sh"
ENTRYPOINT="$(docker inspect "$IMAGE" --format '{{json .Config.Entrypoint}}')"
if [ "$ENTRYPOINT" != '["/entrypoint.sh"]' ]; then
    fail "entrypoint is $ENTRYPOINT (expected [\"/entrypoint.sh\"])"
fi

echo "ok: image build smoke test passed"
