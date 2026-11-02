"""Restore application files, including removing files first introduced by the upgrade."""

import sys
import tarfile
from pathlib import Path


def restore(target: Path, backup: Path):
    target = target.resolve()
    for relative in (backup / "added-files.txt").read_text(encoding="utf-8").splitlines():
        path = target / relative
        if not path.resolve().is_relative_to(target):
            raise ValueError("Invalid rollback path")
        if path.is_file():
            path.unlink()
    with tarfile.open(backup / "code.tar.gz") as archive:
        for member in archive.getmembers():
            if not (target / member.name).resolve().is_relative_to(target):
                raise ValueError("Invalid backup path")
            if not member.isfile():
                raise ValueError("Only regular application files are allowed in the backup")
        archive.extractall(target, filter="data")


if __name__ == "__main__":
    restore(Path(sys.argv[1]), Path(sys.argv[2]))
