#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."
export PATH="$HOME/.local/bin:$PATH"
export PYTHONUTF8=1
if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' 'Install uv first; see README-LINUX.md or docs/linux-deploy.md.' >&2
    exit 1
fi
uv sync --locked --no-dev --python 3.11
.venv/bin/python scripts/linux/configure.py
.venv/bin/python -c 'from ark_gacha.config import Settings; from ark_gacha.render import Renderer; s=Settings(); Renderer(s.data_dir/"avatars", s.font_path); print("CJK font ready.")'
printf '%s\n' 'Ready. Edit .env, then run scripts/linux/start.sh.'
