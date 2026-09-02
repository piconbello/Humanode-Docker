---
name: run-humanode
description: Deploy and operate the Humanode validator Docker image - validate the Telegram bot token and every .env setting before starting, choose the native or ngrok tunnel, insert the session-key seed safely, start the node, and check sync/tunnel/bot status or get the bioauth link. Use when asked to run, start, deploy, set up, configure, validate, or check the status of the Humanode node or validator.
---

# Run the Humanode validator

Single container: `humanode-peer` + bioauth tunnel + Telegram bot, supervised by s6.
Chain data and the keystore live in **`./data`**, bind-mounted into the container.

Everything is driven by `.claude/skills/run-humanode/deploy.sh`. Paths are relative to the repo root.

Most of what goes wrong here is configuration, not Docker — a token that Telegram rejects, a
setting the bot cannot parse, a mnemonic with the wrong word count. The driver checks all of
that **before** anything starts, so failures surface as one clear message instead of a
restart loop in the logs.

## Prerequisites

Docker with the Compose plugin (verified on Docker 29.6.1 / Compose 5.2.0), `curl`, and
**x86_64** — the Dockerfile fetches `humanode-distribution-x86_64-unknown-linux-gnu`,
`Linux-x86_64` tunnel client and `s6-overlay-x86_64`, so there is no arm64 path. A full
mainnet sync needs far more than the ~1.2 GB image; budget tens of GB for `./data`.

## Deployment flow

When deploying, follow this order. Before running `up`, ask the user **all three** of these
in a single prompt:

1. **Tunnel backend** — Native Humanode tunnel (default, no account needed) or ngrok?
   If ngrok, they must provide their authtoken. Run `deploy.sh ngrok <authtoken>` to switch,
   or leave the default for native.

2. **Telegram bot** — They need a bot token (from @BotFather: send `/newbot`, pick a name
   and username ending in `bot`, copy the token) and their numeric Telegram user ID
   (from @userinfobot: send `/start`, copy the number). Both are required.

3. **Seed mnemonic** — After the node is healthy, tell the user to run `seed.sh`.
   It needs a real interactive terminal for the hidden prompt, so **where** they
   run it matters:
   - **Desktop (terminal app open):** `! bash .claude/skills/run-humanode/seed.sh`
     — the `!` launcher pops a GUI window for the hidden prompt.
   - **Headless / SSH server:** Claude's `!` has no controlling terminal and there
     is no GUI to pop, so tell the user to run it in a **separate interactive shell**
     (a second SSH session) from the repo root:
     ```
     bash .claude/skills/run-humanode/seed.sh
     ```
     There stdin is a TTY and the hidden prompt works directly.

   Never ask for the mnemonic in chat. Never run `deploy.sh seed` through Claude's Bash tool.

### Step-by-step

1. `deploy.sh check` — prerequisites
2. `deploy.sh init` — create `.env` and `./data`
3. `deploy.sh validator` — enable validator mode
4. Apply tunnel choice — `deploy.sh ngrok <authtoken>` if ngrok, skip if native
5. `deploy.sh telegram <token> <userid>` — live-verified before writing
6. `deploy.sh pull` — `docker-compose.yml` runs the published image
   `ghcr.io/piconbello/humanode-docker:latest`; set `HMND_IMAGE_TAG` in `.env` to pin a
   release instead. On a clean `main` checkout this pulls that image. On any other branch,
   with uncommitted changes, or if the pull fails, it builds locally and tags the build with
   the same ref, so compose runs the right thing either way. `deploy.sh build` always builds
   locally. Never pull the published image while working on a branch — you would be running
   main's code and debugging changes that are not in the container.
7. `deploy.sh up` — validates config, then starts the container
8. `deploy.sh status` — confirm node, tunnel, and bot are healthy
9. Tell the user to run `seed.sh` to insert their mnemonic — `! bash
   .claude/skills/run-humanode/seed.sh` on a desktop, or `bash
   .claude/skills/run-humanode/seed.sh` in a separate SSH shell on a headless server
10. `deploy.sh link` — get the bioauth URL

## Quick start

```sh
.claude/skills/run-humanode/deploy.sh check                        # docker, compose, curl, disk
.claude/skills/run-humanode/deploy.sh init                         # .env + ./data, then validate
.claude/skills/run-humanode/deploy.sh validator                    # VALIDATOR=true (tunnel + bioauth)
.claude/skills/run-humanode/deploy.sh telegram <token> <userid>    # live-verified before writing
.claude/skills/run-humanode/deploy.sh pull                         # ghcr on clean main, else local build
.claude/skills/run-humanode/deploy.sh up                           # validates, then starts
bash .claude/skills/run-humanode/seed.sh                           # interactive; hidden input
.claude/skills/run-humanode/deploy.sh link
```

## Validating configuration

`validate` runs on its own, inside `init`, and again as a gate inside `up` — `up` refuses to
start an invalid configuration.

```sh
.claude/skills/run-humanode/deploy.sh validate
```

It checks four things:

1. **Telegram token shape** — must match `<digits>:<35+ chars>`, the same rule the bot enforces.
2. **Telegram token validity, live** — calls `api.telegram.org/bot<token>/getMe` and reports
   the real bot username, or the actual rejection:
   ```
   shape                ok (123456:RE...)
   live check           REJECTED (401 Unauthorized) - wrong or revoked token
   ```
3. **Every other setting**, parsed by the project's own `hmnd_bot.config.load_config`, so the
   rules never drift from the bot:
   ```
   INVALID: malformed duration: 'junk' (expected e.g. '10m', '1h', '1d')
   ```
4. **Combinations that cannot work** — e.g. `ENABLE_BOT` set with no token, which would
   restart-loop forever.

Exit status is 0 only when the configuration is usable.

## Telegram bot

**Three settings are required:** `ENABLE_BOT`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_USER_ID`.
They are documented in `.env.example` but **not** in the README.

```sh
.claude/skills/run-humanode/deploy.sh telegram <bot-token> <telegram-user-id>
```

The token is verified against Telegram **before** being written. A malformed token, a
non-numeric user id, or a 401 all abort with nothing written to `.env`:

```
Telegram rejected this token (401); refusing to write it
```

Get a token from @BotFather; get your numeric id from @userinfobot. That the id is *yours*
cannot be checked offline — the driver only proves it is an integer.

## Facescan reminders — on by default

Reminders fire at **1s before** bioauth expires, then after it at:

```
15m, 1h, 3h, 6h, 12h, 1d, 3d, 7d    then every 4d
```

The 1s pre-expiry rung is what gives you a notification at the moment of expiry.

**The two lists use different semantics.** `BIOAUTH_REMIND_BEFORE` is absolute — each value
is a time before expiry. `BIOAUTH_REMIND_AFTER` is **cumulative**: `bioauth.py:142` does
`cum += d`, so each entry is measured from the previous reminder. That is why the default is
stored as `15m,45m,2h,3h,6h,12h,2d,4d` — those deltas produce the absolute schedule above.
Edit with care: `15m,1h` fires at 15m and **1h15m**, not 1h.

The last entry also sets the repeat interval once the ladder is exhausted (`4d` here).

```sh
.claude/skills/run-humanode/deploy.sh reminders on     # restore defaults + stall alerts
.claude/skills/run-humanode/deploy.sh reminders off    # writes BIOAUTH_REMIND_*=off
```

Units are `s`, `m`, `h`, `d`. Zero durations are rejected everywhere.

Stall alerts remain opt-in; `reminders on` turns them on too:

| setting | default | meaning |
|---|---|---|
| `BIOAUTH_REMIND_BEFORE` | `1s` | absolute; fires 1s before expiry |
| `BIOAUTH_REMIND_AFTER` | `15m,45m,2h,3h,6h,12h,2d,4d` | cumulative; fires 15m → 7d, then every 4d |
| `BLOCK_STALL_THRESHOLD` / `_REMIND_AFTER` | `10m` / `30m,1h,2h` | block production stalled |
| `FINALITY_STALL_THRESHOLD` / `_REMIND_AFTER` | `30m` / `1h,2h` | finality stalled |

`validate` reports the **effective** settings by running the project's own config parser, so
it stays correct even if the defaults change:

```
facescan reminders   on
  before expiry      1s
  after expiry       15m,1h,3h,6h,12h,1d,3d,7d
  then every         4d
block stall alerts   off
```

Note it prints the **absolute** fire times, not the cumulative values stored in `.env`.

Each stall pair needs *both* halves set to take effect.

## Bot commands (in Telegram)

| command | does |
|---|---|
| `/link` | fresh bioauth link + QR; refuses while the node is still catching up |
| `/tunnel_status` | connected / running-but-not-connected / down, with the URL |
| `/reconnect_tunnel` | restart the tunnel and issue a new URL |
| `/cancel_tunnel` | ngrok only; refused on the native tunnel (it is s6-supervised) |

## Node tuning

Set in `.env`; all are validated by `deploy.sh validate`.

| variable | default | note |
|---|---|---|
| `SYNC_MODE` | `full` | `full`, `warp`, `fast`, `fast-unsafe` |
| `NODE_NAME` | auto `HND-<word>-<hex>` | telemetry name; persists in `data/.node-name` |
| `DB_CACHE` | `256` | RocksDB cache, MiB |
| `STATE_PRUNING` / `BLOCKS_PRUNING` | binary defaults | `STATE_PRUNING` can only be set on first run |
| `TUNNEL_MAX_IDLE_TIMEOUT` | `60s` | how fast a dead tunnel is detected |

## Upgrading the node

The upstream version is pinned in `artifacts/humanode-version.txt`. The container runs
`ghcr.io/piconbello/humanode-docker:latest`, so `pull` picks up the newest published image;
set `HMND_IMAGE_TAG` in `.env` to stay on a specific release instead.

```sh
.claude/skills/run-humanode/deploy.sh pull
.claude/skills/run-humanode/deploy.sh up
```

`./data` is untouched by a rebuild, so the chain database and keystore survive. The tunnel URL
changes on restart.

## Tunnel choice

Native Humanode tunnel is the default and needs no account:

```sh
.claude/skills/run-humanode/deploy.sh ngrok <authtoken>   # switch to ngrok
.claude/skills/run-humanode/deploy.sh ngrok               # back to native
```

They are mutually exclusive. An ngrok token can only be proven valid at runtime — watch for
`err_ngrok_105` in the logs.

## Insert the session-key seed

Session keys only — **not** the stash/controller seed.

The `seed.sh` wrapper reads the mnemonic from whatever real terminal it can find, in
this order: an interactive stdin (script run directly in a shell), then `/dev/tty` (piped
but still attached to a terminal), and only then a fresh GUI window on a desktop. It needs
one of these to prompt with hidden input.

```sh
bash .claude/skills/run-humanode/seed.sh
```

**On a headless / SSH server** run that in your own interactive shell (a second SSH
session) — **not** through Claude's `!`, which has no controlling terminal, and not
expecting a GUI window to pop. If no terminal is reachable, `seed.sh` prints the exact
command to run manually instead of silently doing nothing.

Equivalent raw form, no wrapper:

```sh
read -rsp 'Seed: ' SEED; echo
printf '%s' "$SEED" | .claude/skills/run-humanode/deploy.sh seed
unset SEED
```

The word count is checked first (12/15/18/21/24); a wrong count aborts without sending
anything to the node. The seed only ever travels on stdin — never argv, never `.env`, never
the logs. Success prints `insert-key: keystore populated for key-type kbai`.

## Status and bioauth

```sh
.claude/skills/run-humanode/deploy.sh status
.claude/skills/run-humanode/deploy.sh link
```

```
peers 8 | role AUTHORITY | blocks 3871 / 19572610 (19568739 behind, 0%)
data on disk 136M
keystore: kbai session key present
tunnel:   native - connected - wss://44bd-....ws1.htunnel.app
bot:      disabled (run: ... telegram <token> <userid>)
```

Other commands: `logs [n]` (ANSI stripped), `down` (keep `./data`), `destroy` (wipe `./data`).

## Data layout

`docker-compose.yml` bind-mounts `./data:/data`. Inside it:

| path | owner | contents |
|---|---|---|
| `data/chains/` | uid 1100 (`hmnd`) | chain database + keystore |
| `data/bot-state/` | uid 1101 (`botuser`) | bot persistence |
| `data/.node-name` | root | generated node name, persists across restarts |

## Gotchas

- **`./data` is created root-owned and you cannot read `data/chains` as your host user.**
  The entrypoint chowns the subdirectories to the container's uids (1100/1101), so
  `ls data/chains` gives `Permission denied` and `rm -rf data` fails. Use
  `deploy.sh destroy`, which wipes it from inside a container instead of needing sudo.
- **Emptying a reminder value does not disable it.** `_optional()` falls back to the default
  on an empty string, so `BIOAUTH_REMIND_BEFORE=` silently keeps the default. Use the literal
  `off` (or `none`). Deleting the line entirely also leaves reminders on.
- **Stall alerts need both halves.** A threshold without its `_REMIND_AFTER` list, or vice
  versa, is silently inert.
- **`ENABLE_BOT` gates the whole bot** and `bot/run` only checks that it is *non-empty* — any
  value works, despite the message saying `true`. It and the two Telegram settings are absent
  from `README.md`.
- **A bad bot token restart-loops forever**, about every 4 seconds, logging
  `TELEGRAM_BOT_TOKEN looks like a placeholder; refusing to start`. The node and tunnel are
  unaffected — only the logs suffer. `validate` catches this before you ever start.
- **Recreating the container changes the tunnel URL**, invalidating any bioauth link already
  sent. The node *name* survives (`data/.node-name`); the URL does not.
- **The native tunnel is WebSocket-only.** An HTTP POST to the public URL returns `400` and
  never reaches the node. Check it locally instead: `:4545/api/v1/public-url` returns
  `200`+URL when connected, `412` when running-but-not-connected.
- **The native tunnel client never retries.** One connect attempt, then it sits forever
  without exiting, so s6 never restarts it. The bot's `TunnelWatcher` is what recovers this;
  `status` shows it as `running but NOT connected`.
- **RPC is loopback-only inside the container** and 9944 is not published, so
  `curl 127.0.0.1:9944` from the host fails by design. Use `docker compose exec`.
- **Logs open with a large ANSI-art banner.** Pipe through `sed 's/\x1b\[[0-9;]*m//g'`;
  `deploy.sh logs` does it for you.
- **Editing `deploy.sh`: `set -o pipefail` + `grep -q` is a trap.** `grep -q` exits early,
  `docker compose logs` takes SIGPIPE, and the pipeline reports failure even on a match.
  Capture the logs into a variable, then grep the variable.

## Bot development

The editable install may point at a **different worktree** — check before trusting a test run:

```sh
python3 -c "import hmnd_bot, os; print(os.path.dirname(hmnd_bot.__file__))"
cd bot && PYTHONPATH=$PWD/src python3 -m pytest -q
```

`tests/test_bioauth_url.py` segfaults inside Pillow's C extension on some hosts — a broken
local Pillow, not a code failure. Run the other files individually to confirm.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `env file ... /.env not found` | `deploy.sh init` |
| `Telegram rejected this token (401)` | re-issue via @BotFather; `deploy.sh telegram` re-verifies |
| `TELEGRAM_BOT_TOKEN looks like a placeholder` repeating in logs | `deploy.sh validate` — real token required |
| `bot: disabled` | `deploy.sh telegram <token> <userid>` |
| bot works but never warns before bioauth expiry | `deploy.sh validate` — check reminders are not set to `off` |
| no stall alerts | both halves of the stall pair must be set |
| `INVALID: malformed duration ...` | fix that line in `.env`; format is `30s`, `10m`, `1h`, `1d` |
| `expected a 12/15/18/21/24-word mnemonic` | wrong seed; nothing was sent to the node |
| `error: insert-key accepts seed via stdin only` | pipe the seed, don't pass it as an argument |
| `kbai keystore entry already exists` | `deploy.sh destroy` wipes `./data`, then re-insert |
| `ls: cannot open directory 'data/chains/'` | expected; owned by uid 1100 inside the container |
| tunnel shows `412` | it retries; `deploy.sh up` forces a restart |
| `manifest unknown` on pull | `HMND_IMAGE_TAG` names a tag that was never published; check the repo's releases |
| running an older image than expected | `latest` is only republished on pushes to `main`; `deploy.sh pull` then `up` |
| container is running code you just changed | `up` reuses the existing image; `deploy.sh build` first |
| pulled image looks like your local edits | a local build carries the same pinned ref; `docker image rm <ref>`, then `deploy.sh pull` on clean `main` |
| Disk filling up | image ~1.2 GB, a full sync needs far more; `deploy.sh destroy` reclaims `./data` |
