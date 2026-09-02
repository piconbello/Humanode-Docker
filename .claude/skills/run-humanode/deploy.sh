#!/usr/bin/env bash
set -euo pipefail
export MSYS_NO_PATHCONV=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

SERVICE="humanode"
DATA_DIR="$ROOT/data"

c_red() { printf '\033[31m%s\033[0m\n' "$*"; }
c_grn() { printf '\033[32m%s\033[0m\n' "$*"; }
c_ylw() { printf '\033[33m%s\033[0m\n' "$*"; }
hdr()   { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

dc() { docker compose "$@"; }

need_env() { [ -f .env ] || { c_red "no .env; run: $0 init"; exit 1; }; }

env_get() {
    [ -f .env ] || return 0
    sed -n "s/^$1=//p" .env | tail -1
}

env_set() {
    local key="$1" val="$2"
    sed -i "/^${key}=/d" .env
    printf '%s=%s\n' "$key" "$val" >> .env
}

rpc() {
    dc exec -T "$SERVICE" curl -sS --max-time 5 \
        -H 'Content-Type: application/json' \
        -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"$1\",\"params\":[]}" \
        http://127.0.0.1:9944 2>/dev/null
}

running() {
    local id
    id=$(dc ps -q "$SERVICE" 2>/dev/null) || return 1
    [ -n "$id" ] && [ "$(docker inspect -f '{{.State.Running}}' "$id" 2>/dev/null)" = "true" ]
}

check_telegram_token() {
    local token="$1" resp
    resp=$(curl -sS --max-time 10 "https://api.telegram.org/bot${token}/getMe" 2>/dev/null || true)
    case "$resp" in
        *'"ok":true'*)
            printf '%s' "$resp" | sed -n 's/.*"username":"\([^"]*\)".*/\1/p'
            return 0 ;;
        *'"error_code":401'*) return 1 ;;
        *'"error_code":404'*) return 2 ;;
        '')                   return 3 ;;
        *)                    return 4 ;;
    esac
}

cmd_check() {
    hdr "prerequisites"
    docker version --format 'docker      {{.Server.Version}}' 2>/dev/null || { c_red "docker not available"; exit 1; }
    docker compose version --short 2>/dev/null | sed 's/^/compose     /' || { c_red "docker compose plugin missing"; exit 1; }
    command -v curl >/dev/null && printf 'curl        present\n' || c_red "curl missing (needed for token checks)"
    local avail
    avail=$(df -BG --output=avail "$ROOT" | tail -1 | tr -dc '0-9')
    printf 'disk free   %sG  (at %s)\n' "$avail" "$ROOT"
    [ "$avail" -lt 50 ] && c_ylw "warning: a full mainnet sync needs well over ${avail}G" || true
}

cmd_init() {
    hdr "init"
    if [ -f .env ]; then
        c_grn ".env exists (left untouched)"
    else
        cp .env.example .env
        c_grn "created .env from .env.example"
    fi
    if [ -d "$DATA_DIR" ]; then
        c_grn "data directory exists: ./data"
    else
        mkdir -p "$DATA_DIR"
        c_grn "created data directory: ./data"
    fi
    cmd_validate || true
}

cmd_validate() {
    need_env
    local rc=0

    hdr "required settings"
    local token userid enable
    token=$(env_get TELEGRAM_BOT_TOKEN)
    userid=$(env_get TELEGRAM_USER_ID)
    enable=$(env_get ENABLE_BOT)

    if [ -z "$(env_get VALIDATOR)" ]; then
        c_ylw "VALIDATOR            not set   -> node only, no tunnel, no bioauth"
    else
        c_grn "VALIDATOR            $(env_get VALIDATOR)"
    fi

    if [ -z "$enable" ]; then
        c_ylw "ENABLE_BOT           not set   -> telegram bot will not start"
    else
        c_grn "ENABLE_BOT           $enable"
    fi

    hdr "telegram token"
    if [ -z "$token" ]; then
        c_ylw "TELEGRAM_BOT_TOKEN   not set"
        [ -n "$enable" ] && { c_red "ENABLE_BOT is set but there is no token; the bot will restart-loop"; rc=1; }
    elif ! printf '%s' "$token" | grep -qE '^[0-9]{6,12}:[A-Za-z0-9_-]{30,}$'; then
        c_red "TELEGRAM_BOT_TOKEN   malformed (expected <digits>:<35+ chars>)"
        c_red "                     the bot refuses to start on a malformed token"
        rc=1
    else
        printf 'shape                ok (%s...)\n' "$(printf '%s' "$token" | cut -c1-9)"
        local name status
        set +e
        name=$(check_telegram_token "$token"); status=$?
        set -e
        case "$status" in
            0) c_grn "live check           VALID -> @${name}" ;;
            1) c_red "live check           REJECTED (401 Unauthorized) - wrong or revoked token"; rc=1 ;;
            2) c_red "live check           404 - token is not a real bot token"; rc=1 ;;
            3) c_ylw "live check           skipped - no network to api.telegram.org" ;;
            *) c_ylw "live check           unexpected response from Telegram" ;;
        esac
    fi

    hdr "telegram user id"
    if [ -z "$userid" ]; then
        c_ylw "TELEGRAM_USER_ID     not set"
        [ -n "$enable" ] && { c_red "ENABLE_BOT is set but there is no user id; the bot will refuse to start"; rc=1; }
    elif ! printf '%s' "$userid" | grep -qE '^-?[0-9]+$'; then
        c_red "TELEGRAM_USER_ID     '$userid' is not an integer"
        rc=1
    else
        c_grn "TELEGRAM_USER_ID     $userid"
        c_ylw "                     numeric only; that it is YOUR id cannot be checked offline"
    fi

    hdr "effective configuration"
    local parsed prc
    set +e
    parsed=$(cd bot && PYTHONPATH="$PWD/src" python3 - "$ROOT/.env" <<'PY' 2>&1
import sys
from datetime import timedelta
from hmnd_bot.config import load_config, ConfigError
env = {}
for line in open(sys.argv[1]):
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    env[k.strip()] = v.strip()
env.setdefault("TELEGRAM_BOT_TOKEN", "0:placeholderplaceholderplaceholderx")
env.setdefault("TELEGRAM_USER_ID", "0")

def fmt(ds):
    out = []
    for d in ds:
        t = int(d.total_seconds())
        if t % 86400 == 0: out.append(f"{t//86400}d")
        elif t % 3600 == 0: out.append(f"{t//3600}h")
        elif t % 60 == 0: out.append(f"{t//60}m")
        else: out.append(f"{t}s")
    return ",".join(out)

try:
    c = load_config(env)
except (ConfigError, ValueError) as e:
    print(f"PARSE=INVALID: {e}")
    sys.exit(0)
print("PARSE=OK")
print(f"BIOAUTH={'on' if (c.bioauth_remind_before or c.bioauth_remind_after) else 'off'}")
if c.bioauth_remind_before:
    print(f"BEFORE={fmt(c.bioauth_remind_before)}")
if c.bioauth_remind_after:
    cum, fires = timedelta(), []
    for d in c.bioauth_remind_after:
        cum += d
        fires.append(cum)
    print(f"AFTER={fmt(fires)}")
    print(f"REPEAT={fmt([c.bioauth_remind_after[-1]])}")
print(f"BLOCKSTALL={'on' if c.block_stall_threshold and c.block_stall_remind_after else 'off'}")
print(f"FINALITY={'on' if c.finality_stall_threshold and c.finality_stall_remind_after else 'off'}")
PY
)
    prc=$?
    set -e

    if [ $prc -ne 0 ]; then
        c_ylw "could not run the config parser (python3 missing?)"
    else
        local parse_line
        parse_line=$(printf '%s' "$parsed" | sed -n 's/^PARSE=//p')
        if [ "$parse_line" = "OK" ]; then
            c_grn "all tunables parse cleanly"
        else
            c_red "$parse_line"
            rc=1
        fi

        hdr "reminders"
        if [ "$(printf '%s' "$parsed" | sed -n 's/^BIOAUTH=//p')" = "on" ]; then
            c_grn "facescan reminders   on"
            local before after repeat
            before=$(printf '%s' "$parsed" | sed -n 's/^BEFORE=//p')
            after=$(printf '%s' "$parsed" | sed -n 's/^AFTER=//p')
            repeat=$(printf '%s' "$parsed" | sed -n 's/^REPEAT=//p')
            [ -n "$before" ] && printf '  before expiry      %s\n' "$before" || printf '  before expiry      (none)\n'
            [ -n "$after" ] && printf '  after expiry       %s\n' "$after"
            [ -n "$repeat" ] && printf '  then every         %s\n' "$repeat"
        else
            c_ylw "facescan reminders   OFF - explicitly disabled"
            [ -n "$enable" ] && c_ylw "                     the bot answers /link but never warns before expiry"
            printf '                     re-enable with: %s reminders on\n' "$0"
        fi
        [ "$(printf '%s' "$parsed" | sed -n 's/^BLOCKSTALL=//p')" = "on" ] \
            && c_grn "block stall alerts   on" || c_ylw "block stall alerts   off  ($0 reminders on)"
        [ "$(printf '%s' "$parsed" | sed -n 's/^FINALITY=//p')" = "on" ] \
            && c_grn "finality alerts      on" || c_ylw "finality alerts      off  ($0 reminders on)"
    fi

    hdr "tunnel"
    if [ -n "$(env_get NGROK_AUTHTOKEN)" ]; then
        c_grn "backend              ngrok (NGROK_AUTHTOKEN set)"
        printf '                     validity is only provable at runtime; watch for err_ngrok_105\n'
    else
        c_grn "backend              native humanode tunnel (no account needed)"
    fi

    hdr "verdict"
    if [ $rc -eq 0 ]; then c_grn "configuration is usable"; else c_red "fix the errors above before '$0 up'"; fi
    return $rc
}

cmd_validator() {
    need_env
    if grep -q '^#VALIDATOR=true' .env; then
        sed -i 's/^#VALIDATOR=true/VALIDATOR=true/' .env
    else
        env_set VALIDATOR true
    fi
    c_grn "VALIDATOR=true set"
}

cmd_telegram() {
    need_env
    local token="${1:-}" userid="${2:-}"
    if [ -z "$token" ] || [ -z "$userid" ]; then
        c_red "usage: $0 telegram <bot-token> <telegram-user-id>"
        exit 1
    fi
    if ! printf '%s' "$userid" | grep -qE '^-?[0-9]+$'; then
        c_red "telegram user id must be an integer, got: $userid"
        exit 1
    fi
    if ! printf '%s' "$token" | grep -qE '^[0-9]{6,12}:[A-Za-z0-9_-]{30,}$'; then
        c_red "token is malformed (expected <digits>:<35+ chars>); refusing to write it"
        exit 1
    fi
    local name status
    set +e
    name=$(check_telegram_token "$token"); status=$?
    set -e
    case "$status" in
        0) c_grn "token valid -> @${name}" ;;
        1) c_red "Telegram rejected this token (401); refusing to write it"; exit 1 ;;
        2) c_red "Telegram returned 404; this is not a bot token"; exit 1 ;;
        3) c_ylw "no network to Telegram; writing without a live check" ;;
        *) c_ylw "unexpected Telegram response; writing anyway" ;;
    esac
    env_set ENABLE_BOT true
    env_set TELEGRAM_BOT_TOKEN "$token"
    env_set TELEGRAM_USER_ID "$userid"
    c_grn "telegram configured; run '$0 up' to apply"
}

cmd_reminders() {
    need_env
    case "${1:-on}" in
        on)
            sed -i '/^BIOAUTH_REMIND_/d' .env
            env_set BLOCK_STALL_THRESHOLD "10m"
            env_set BLOCK_STALL_REMIND_AFTER "30m,1h,2h"
            env_set FINALITY_STALL_THRESHOLD "30m"
            env_set FINALITY_STALL_REMIND_AFTER "1h,2h"
            c_grn "facescan reminders restored to defaults; stall alerts enabled"
            ;;
        off)
            env_set BIOAUTH_REMIND_BEFORE off
            env_set BIOAUTH_REMIND_AFTER off
            sed -i '/^BLOCK_STALL_/d;/^FINALITY_STALL_/d' .env
            c_ylw "facescan reminders disabled (bot answers commands only)"
            c_ylw "note: deleting the lines is not enough - reminders are ON by default"
            ;;
        *) c_red "usage: $0 reminders [on|off]"; exit 1 ;;
    esac
}

cmd_ngrok() {
    need_env
    local token="${1:-}"
    if [ -z "$token" ]; then
        sed -i '/^NGROK_AUTHTOKEN=/d' .env
        c_grn "ngrok disabled; using the native humanode tunnel"
    else
        env_set NGROK_AUTHTOKEN "$token"
        c_grn "ngrok enabled; run '$0 up' to apply"
        c_ylw "note: an ngrok token can only be proven valid at runtime"
    fi
}

cmd_build() { hdr "build"; dc build; }

git_branch() { git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'unknown'; }

# The image docker-compose.yml will actually run, tag interpolation included.
image_ref() { dc config --images 2>/dev/null | head -1; }

# Pull the published image only when this checkout matches what produced it.
# GHCR is published from pushes to main, so anywhere else it would run code that
# is not the code in front of you. Every other case builds locally, which tags
# the build with the same ref so compose picks it up either way.
cmd_pull() {
    hdr "pull"
    local image branch
    image="$(image_ref)"
    [ -n "$image" ] || { c_red "could not resolve the image from docker-compose.yml"; exit 1; }
    printf 'image       %s\n' "$image"
    branch="$(git_branch)"

    if [ "$branch" != "main" ]; then
        c_ylw "on branch '${branch}', not main"
        c_ylw "the published image is built from main and would not contain this checkout's changes"
        c_ylw "building locally instead"
        cmd_build
        return
    fi

    if [ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ]; then
        c_ylw "working tree has uncommitted changes; the published image would not match them"
        c_ylw "building locally instead"
        cmd_build
        return
    fi

    if docker pull "$image"; then
        c_grn "pulled $image"
        return
    fi

    c_ylw "pull failed (offline, package not public, or no such tag); building locally instead"
    cmd_build
}

cmd_up() {
    need_env
    [ -d "$DATA_DIR" ] || mkdir -p "$DATA_DIR"
    cmd_validate || { c_red "refusing to start with an invalid configuration"; exit 1; }
    hdr "up"
    dc up -d
    printf 'waiting for node RPC'
    local i
    for i in $(seq 1 60); do
        if rpc system_health | grep -q '"peers"'; then printf '\n'; c_grn "node RPC is up"; return 0; fi
        printf '.'; sleep 3
    done
    printf '\n'; c_red "node RPC did not come up within 180s; check: $0 logs"
    return 1
}

cmd_seed() {
    if [ -t 0 ]; then
        c_red "pipe the seed in; do not type it interactively"
        printf 'usage: printf %%s "$SEED" | %s seed\n' "$0"
        exit 1
    fi
    local seed words
    seed=$(cat)
    words=$(printf '%s' "$seed" | tr -s '[:space:]' '\n' | grep -c . || true)
    case "$words" in
        12|15|18|21|24) c_grn "seed looks like a $words-word mnemonic" ;;
        *) c_red "expected a 12/15/18/21/24-word mnemonic, counted $words words"
           c_red "refusing to insert; nothing was sent to the node"
           unset seed; exit 1 ;;
    esac
    running || { c_red "container is not running; run: $0 up"; unset seed; exit 1; }
    printf '%s' "$seed" | dc exec -T "$SERVICE" /usr/local/bin/insert-key.sh
    unset seed
}

cmd_status() {
    hdr "container"
    dc ps --format 'table {{.Name}}\t{{.Status}}' 2>/dev/null || true
    running || { c_red "not running"; return 1; }

    hdr "node"
    local health sync cur high peers role
    health=$(rpc system_health || true)
    sync=$(rpc system_syncState || true)
    peers=$(printf '%s' "$health" | grep -o '"peers":[0-9]*' | cut -d: -f2)
    cur=$(printf '%s' "$sync" | grep -o '"currentBlock":[0-9]*' | cut -d: -f2)
    high=$(printf '%s' "$sync" | grep -o '"highestBlock":[0-9]*' | cut -d: -f2)
    role=$(dc logs "$SERVICE" 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | grep -a 'Role:' | tail -1 | sed 's/.*Role: *//;s/ *$//')
    printf 'peers        %s\n' "${peers:-?}"
    printf 'role         %s\n' "${role:-?}"
    if [ -n "${cur:-}" ] && [ -n "${high:-}" ] && [ "$high" -gt 0 ]; then
        printf 'blocks       %s / %s  (%s behind, %s%%)\n' \
            "$cur" "$high" "$((high - cur))" "$((cur * 100 / high))"
        [ "$((high - cur))" -lt 20 ] && c_grn "at the chain tip" || c_ylw "still catching up"
    fi
    printf 'data on disk %s\n' "$(dc exec -T "$SERVICE" du -sh /data 2>/dev/null | cut -f1)"

    hdr "keystore"
    if dc exec -T "$SERVICE" sh -c 'find /data/chains -type f -path "*/keystore/6b626169*" | grep -q .' 2>/dev/null; then
        c_grn "kbai session key present"
    else
        c_ylw "no session key; run: printf %s \"\$SEED\" | $0 seed"
    fi

    hdr "tunnel"
    local code url
    if [ -n "$(env_get NGROK_AUTHTOKEN)" ]; then
        printf 'backend      ngrok\n'
        dc exec -T "$SERVICE" curl -sS --max-time 5 http://127.0.0.1:4040/api/tunnels/command_line 2>/dev/null \
            | grep -o '"public_url":"[^"]*"' || c_ylw "no ngrok tunnel reported"
    else
        printf 'backend      native (humanode-websocket-tunnel)\n'
        code=$(dc exec -T "$SERVICE" curl -sS --max-time 5 -o /dev/null -w '%{http_code}' \
            http://127.0.0.1:4545/api/v1/public-url 2>/dev/null || echo 000)
        case "$code" in
            200) url=$(dc exec -T "$SERVICE" curl -sS --max-time 5 http://127.0.0.1:4545/api/v1/public-url 2>/dev/null)
                 c_grn "connected"; printf 'url          %s\n' "$url" ;;
            412) c_ylw "running but NOT connected to the relay (retrying)" ;;
            *)   c_red "tunnel API unreachable (client down?)" ;;
        esac
    fi

    hdr "bot"
    local blog
    blog=$(dc logs "$SERVICE" 2>&1 | sed 's/\x1b\[[0-9;]*m//g' || true)
    if printf '%s' "$blog" | grep -aqF 'bot: disabled'; then
        c_ylw "disabled (run: $0 telegram <token> <userid>)"
    elif printf '%s' "$blog" | grep -aqF 'looks like a placeholder'; then
        c_red "restart-looping: TELEGRAM_BOT_TOKEN is a placeholder or malformed"
    elif printf '%s' "$blog" | grep -aqF 'Telegram token rejected'; then
        c_red "restart-looping: Telegram rejected the token (401)"
    elif printf '%s' "$blog" | grep -aqF 'telegram preflight ok'; then
        c_grn "running"
        printf '%s' "$blog" | grep -aF 'telegram preflight ok' | tail -1
    else
        c_ylw "state unknown; check: $0 logs"
    fi
}

cmd_link() {
    running || { c_red "container is not running"; exit 1; }
    local url
    url=$(dc exec -T "$SERVICE" curl -sS --max-time 5 http://127.0.0.1:4545/api/v1/public-url 2>/dev/null || true)
    if [ -z "$url" ]; then
        dc logs "$SERVICE" 2>&1 | grep -a 'bioauth link:' | tail -1
        return
    fi
    printf 'https://webapp.mainnet.stages.humanode.io/open?url=%s\n' "$url"
}

cmd_logs() { dc logs -f --tail "${1:-100}" "$SERVICE" 2>&1 | sed 's/\x1b\[[0-9;]*m//g'; }

cmd_down() { hdr "down"; dc down; }

cmd_destroy() {
    hdr "destroy"
    dc down 2>/dev/null || true
    if [ -d "$DATA_DIR" ]; then
        docker run --rm -v "$DATA_DIR:/d" busybox sh -c 'rm -rf /d/* /d/.[!.]* 2>/dev/null || true'
        c_grn "wiped ./data (chain database and keystore are gone)"
    else
        c_ylw "./data does not exist"
    fi
}

usage() {
    cat <<'EOF'
usage: .claude/skills/run-humanode/deploy.sh <command>

setup and validation
  check                     docker, compose, curl, disk
  init                      create .env + ./data, then validate
  validate                  check every setting, live-verify the telegram token
  validator                 set VALIDATOR=true
  telegram <token> <userid> live-verify the token, then write bot settings
  reminders [on|off]        facescan reminders + stall alerts (OFF by default)
  ngrok [token]             use ngrok (token) or the native tunnel (no args)

running
  pull                      pull the pinned image on a clean main, else build locally
  build                     always build locally (docker compose build)
  up                        validate, start, wait for node RPC
  seed                      insert session key (mnemonic on stdin ONLY)
  status                    node / keystore / tunnel / bot
  link                      print the current bioauth URL
  logs [n]                  follow logs, ANSI stripped
  down                      stop, keep ./data
  destroy                   stop and WIPE ./data
EOF
}

case "${1:-}" in
    check) cmd_check ;;
    init) cmd_init ;;
    validate) cmd_validate ;;
    validator) cmd_validator ;;
    telegram) shift; cmd_telegram "${1:-}" "${2:-}" ;;
    reminders) shift; cmd_reminders "${1:-on}" ;;
    ngrok) shift; cmd_ngrok "${1:-}" ;;
    pull) cmd_pull ;;
    build) cmd_build ;;
    up) cmd_up ;;
    seed) cmd_seed ;;
    status) cmd_status ;;
    link) cmd_link ;;
    logs) shift; cmd_logs "${1:-100}" ;;
    down) cmd_down ;;
    destroy) cmd_destroy ;;
    *) usage; [ -n "${1:-}" ] && exit 1 || exit 0 ;;
esac
