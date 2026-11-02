#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

package_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
target="$(cd -- "${1:?Usage: bash upgrade.sh /absolute/existing/deployment}" && pwd -P)"
base_image=ark-gacha-simulator:0.1.2
new_image=ark-gacha-simulator:0.1.3

for command in docker python3 tar; do command -v "$command" >/dev/null; done
python3 -B "$package_dir/verify.py"
test -f "$target/.env"
test -f "$target/compose.yaml"
test -d "$target/data"
test "$target" != "$package_dir"
python3 - "$target" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
if 'version = "0.1.2"' not in (root / "pyproject.toml").read_text(encoding="utf-8"):
    raise SystemExit("This upgrade requires an existing 0.1.2 deployment.")
PY
docker compose version >/dev/null
docker compose --project-directory "$target" config --quiet
docker image inspect "$base_image" >/dev/null
test "$(docker run --rm --network none --entrypoint python "$base_image" -c \
  'from importlib.metadata import version; print(version("ark-gacha-simulator"))')" = 0.1.2

lock_dir="$target/.incremental-upgrade.lock"
mkdir -- "$lock_dir" || { printf '%s\n' 'Another upgrade may be in progress.' >&2; exit 1; }
trap 'rmdir -- "$lock_dir"' EXIT
stopped=0
overlay_started=0
backup=""

rollback_on_error() {
    result=$?
    trap - ERR
    set +e
    if (( stopped )); then
        printf '%s\n' 'Upgrade failed; restoring previous application.' >&2
        if (( overlay_started )); then
            docker compose --project-directory "$target" stop bot
            tar -xzf "$backup/code.tar.gz" -C "$target"
        fi
        docker compose --project-directory "$target" up -d --no-build --pull never --force-recreate bot
    fi
    printf 'Upgrade failed. Backup directory: %s\n' "${backup:-not yet created}" >&2
    exit "$result"
}
trap rollback_on_error ERR

# The existing local base supplies Python, libraries and fonts. No registry or pip access.
docker build --pull=false --network=none --tag "$new_image" "$package_dir"
test "$(docker run --rm --network none --entrypoint python "$new_image" -c \
  'from importlib.metadata import version; print(version("ark-gacha-simulator"))')" = 0.1.3

backup_parent="$(dirname -- "$target")/ark-gacha-backups"
mkdir -p -- "$backup_parent"
backup="$(mktemp -d "$backup_parent/before-0.1.3-$(date +%Y%m%d-%H%M%S)-XXXXXX")"
printf '%s\n' "$target" > "$backup/target.txt"
python3 - "$package_dir" "$target" "$backup/code-files.txt" <<'PY'
import json
import sys
from pathlib import Path

package, target, output = map(Path, sys.argv[1:])
manifest = json.loads((package / "MANIFEST.json").read_text(encoding="utf-8"))
files = [name.removeprefix("overlay/") for name in manifest["sha256"] if name.startswith("overlay/")]
output.write_text("".join(name + "\n" for name in files if (target / name).is_file()), encoding="utf-8")
PY
tar -czf "$backup/code.tar.gz" -C "$target" -T "$backup/code-files.txt"
docker compose --project-directory "$target" stop bot
stopped=1
tar -czf "$backup/state.tar.gz" -C "$target" .env data
overlay_started=1
cp -R -- "$package_dir/overlay/." "$target/"
docker compose --project-directory "$target" up -d --no-build --pull never --force-recreate bot

ready=0
for (( attempt=0; attempt<30; attempt++ )); do
    if docker compose --project-directory "$target" exec -T bot python -c \
      'import os,socket; from importlib.metadata import version; assert version("ark-gacha-simulator")=="0.1.3"; socket.create_connection(("127.0.0.1",int(os.getenv("ARK_PORT","8080"))),3).close()' \
      >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done
test "$ready" = 1
stopped=0
docker compose --project-directory "$target" ps
printf 'Upgrade complete: 0.1.3\nBackup: %s\n' "$backup"
