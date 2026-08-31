#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY="$SCRIPT_DIR/deploy.sh"

# If stdin is a TTY we can read directly (normal terminal).
if [ -t 0 ] && [ -t 1 ]; then
    read -rsp 'Seed mnemonic: ' SEED; echo
    if printf '%s' "$SEED" | bash "$DEPLOY" seed; then
        unset SEED
        read -rp "Success. Press Enter to close."
    else
        unset SEED
        read -rp "Failed. Press Enter to close."
    fi
    exit $?
fi

# No TTY (e.g. Claude Code's ! command) — open a new terminal window.
# Convert Git Bash path to Windows path for Windows launchers.
WIN_SCRIPT="$(cygpath -w "$SCRIPT_DIR/seed.sh" 2>/dev/null || echo "$SCRIPT_DIR/seed.sh")"

launch_terminal() {
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*)
            if command -v powershell.exe >/dev/null 2>&1; then
                powershell.exe -NoProfile -Command \
                    "Start-Process -FilePath 'bash' -ArgumentList '-l','$WIN_SCRIPT' -Wait" &
            elif command -v cmd.exe >/dev/null 2>&1; then
                start "" bash -l "$SCRIPT_DIR/seed.sh"
            else
                return 1
            fi
            ;;
        Darwin*)
            osascript -e "tell app \"Terminal\" to do script \"bash '$SCRIPT_DIR/seed.sh'\""
            ;;
        Linux*)
            if command -v x-terminal-emulator >/dev/null 2>&1; then
                x-terminal-emulator -e bash -c "bash '$SCRIPT_DIR/seed.sh'" &
            elif command -v gnome-terminal >/dev/null 2>&1; then
                gnome-terminal -- bash -c "bash '$SCRIPT_DIR/seed.sh'"
            elif command -v xterm >/dev/null 2>&1; then
                xterm -e bash -c "bash '$SCRIPT_DIR/seed.sh'" &
            elif command -v konsole >/dev/null 2>&1; then
                konsole -e bash -c "bash '$SCRIPT_DIR/seed.sh'" &
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
    echo "Opened a terminal window for seed input."
    echo "Paste your mnemonic there — input is hidden for security."
else
    echo "Could not open a terminal window automatically."
    echo "Please open a terminal manually and run:"
    echo "  bash $SCRIPT_DIR/seed.sh"
fi
