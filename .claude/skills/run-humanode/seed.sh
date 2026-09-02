#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY="$SCRIPT_DIR/deploy.sh"

# Pipe a hidden mnemonic to deploy.sh on stdin. The seed never lands in argv,
# .env, or the logs — only ever on a pipe. Returns deploy.sh's exit status.
insert_seed() {
    local SEED rc
    SEED="$1"
    printf '%s' "$SEED" | bash "$DEPLOY" seed
    rc=$?
    unset SEED
    return $rc
}

# Only pause when we were relaunched into a throwaway GUI window, so the user
# can read the result before the window closes. Never pauses for inline runs.
pause_if_window() {
    if [ "${HMND_SEED_WINDOW:-}" = "1" ] && [ -t 0 ]; then
        read -rp "$1 Press Enter to close." _
    fi
}

# ── 1) stdin is an interactive terminal ──────────────────────────────────────
# Script run directly in a shell — including a headless SSH session. This is
# the reliable path on a server.
if [ -t 0 ]; then
    read -rsp 'Seed mnemonic: ' SEED; echo >&2
    if insert_seed "$SEED"; then unset SEED; pause_if_window "Success."; exit 0; fi
    unset SEED; pause_if_window "Failed."; exit 1
fi

# ── 2) No stdin TTY, but a controlling terminal exists ───────────────────────
# e.g. the script is piped but still attached to a terminal. Read the seed
# straight from /dev/tty — no GUI required. This is what makes a headless
# server work without popping a window.
if { exec 3<>/dev/tty; } 2>/dev/null; then
    printf 'Seed mnemonic: ' >&3
    read -rs -u 3 SEED; printf '\n' >&3
    exec 3>&-
    if insert_seed "$SEED"; then unset SEED; exit 0; fi
    unset SEED; exit 1
fi

# ── 3) No terminal at all (e.g. Claude Code's ! command) ─────────────────────
# Hidden input is impossible here. On a desktop we can relaunch into a GUI
# terminal window; on a headless server that cannot work, so we always also
# print the exact command to run manually and never claim false success.
WIN_SCRIPT="$(cygpath -w "$SCRIPT_DIR/seed.sh" 2>/dev/null || echo "$SCRIPT_DIR/seed.sh")"

launch_terminal() {
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*)
            if command -v powershell.exe >/dev/null 2>&1; then
                HMND_SEED_WINDOW=1 powershell.exe -NoProfile -Command \
                    "Start-Process -FilePath 'bash' -ArgumentList '-l','$WIN_SCRIPT' -Wait" &
            elif command -v cmd.exe >/dev/null 2>&1; then
                HMND_SEED_WINDOW=1 start "" bash -l "$SCRIPT_DIR/seed.sh"
            else
                return 1
            fi
            ;;
        Darwin*)
            osascript -e "tell app \"Terminal\" to do script \"HMND_SEED_WINDOW=1 bash '$SCRIPT_DIR/seed.sh'\"" >/dev/null
            ;;
        Linux*)
            # A GUI terminal is only viable with a live X/Wayland session.
            # A DISPLAY forwarded over SSH with no local X server is not one,
            # so we do NOT trust a backgrounded launcher's exit code — the
            # manual instructions below are the real fallback on a server.
            [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] || return 1
            if command -v x-terminal-emulator >/dev/null 2>&1; then
                HMND_SEED_WINDOW=1 x-terminal-emulator -e bash -c "bash '$SCRIPT_DIR/seed.sh'" &
            elif command -v gnome-terminal >/dev/null 2>&1; then
                HMND_SEED_WINDOW=1 gnome-terminal -- bash -c "bash '$SCRIPT_DIR/seed.sh'" &
            elif command -v konsole >/dev/null 2>&1; then
                HMND_SEED_WINDOW=1 konsole -e bash -c "bash '$SCRIPT_DIR/seed.sh'" &
            elif command -v xterm >/dev/null 2>&1; then
                HMND_SEED_WINDOW=1 xterm -e bash -c "bash '$SCRIPT_DIR/seed.sh'" &
            else
                return 1
            fi
            ;;
        *)
            return 1
            ;;
    esac
}

if launch_terminal; then
    echo "Tried to open a terminal window for seed input."
    echo "If no window appeared (common on a headless server), run this in your"
    echo "own terminal instead:"
else
    echo "No interactive terminal is available here."
    echo "Run this directly in your own terminal (e.g. your SSH session):"
fi
echo
echo "  bash $SCRIPT_DIR/seed.sh"
echo
echo "Input is hidden. Enter your 12/15/18/21/24-word session-key mnemonic."
exit 0
