#!/usr/bin/env bash
set -euo pipefail
DEPLOY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deploy.sh"
read -rsp 'Seed mnemonic: ' SEED; echo
printf '%s' "$SEED" | bash "$DEPLOY" seed
unset SEED
