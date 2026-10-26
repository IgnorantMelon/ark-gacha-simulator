#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."
export PYTHONUTF8=1
export PYTHONUNBUFFERED=1
if [[ ! -f .env || ! -x .venv/bin/ark-gacha ]]; then
    printf '%s\n' 'Run scripts/linux/setup.sh first.' >&2
    exit 1
fi
exec .venv/bin/ark-gacha run
