#!/usr/bin/env python3
"""Initialize Linux config and public seed data without copying personal state."""

import argparse
import os
import secrets
import shutil
from pathlib import Path


def initialize(root: Path) -> dict:
    root = root.resolve()
    config = root / ".env"
    template = (root / ".env.example").read_text(encoding="utf-8")
    created = False
    try:
        descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            template = template.replace(
                "ARK_ONEBOT_TOKEN=", "ARK_ONEBOT_TOKEN=" + secrets.token_hex(32)
            )
            stream.write(template.rstrip() + "\n")
            # Match the owner of the bind-mounted directory rather than assuming UID 1000.
            stream.write(f"DEPLOY_UID={getattr(os, 'getuid', lambda: 1000)()}\n")
            stream.write(f"DEPLOY_GID={getattr(os, 'getgid', lambda: 1000)()}\n")
        created = True

    data = root / "data"
    data.mkdir(exist_ok=True)
    seed = root / "seed"
    seeded = False
    if seed.is_dir():
        # Preserve the installed catalog on upgrades. Copy the pointer last so an
        # interrupted first initialization never advertises incomplete seed data.
        pointer = data / "catalog" / "current"
        if not pointer.exists():
            source = seed / "catalog"
            for path in sorted(source.rglob("*")):
                if path.is_file() and path.name != "current":
                    destination = data / "catalog" / path.relative_to(source)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, destination)
            pointer.parent.mkdir(exist_ok=True)
            shutil.copyfile(source / "current", pointer)
            seeded = True
        for source in (seed / "avatars").glob("*.png"):
            destination = data / "avatars" / source.name
            destination.parent.mkdir(exist_ok=True)
            if not destination.exists():
                shutil.copyfile(source, destination)
    return {"config_created": created, "catalog_seeded": seeded, "data_dir": str(data)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    result = initialize(args.root)
    print("Created .env with a new token." if result["config_created"] else "Kept existing .env.")
    print("Imported public seed catalog." if result["catalog_seeded"] else "Kept existing catalog.")
    print("Configure ARK_ADMIN_QQ and ARK_ALLOWED_GROUPS before starting the bot.")


if __name__ == "__main__":
    main()
