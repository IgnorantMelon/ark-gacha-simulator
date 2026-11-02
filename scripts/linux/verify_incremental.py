"""Verify the complete incremental payload before building or changing the deployment."""

import hashlib
import json
from pathlib import Path


def verify(root: Path) -> dict:
    manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    expected = set(manifest["sha256"]) | {"MANIFEST.json"}
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Symlinks are not allowed in the upgrade package")
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    if actual != expected:
        raise ValueError("Unexpected or missing files in the upgrade package")
    for relative, digest in manifest["sha256"].items():
        path = root / relative
        if path.resolve().is_relative_to(root.resolve()) is False:
            raise ValueError("Invalid path in the upgrade manifest")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Upgrade checksum mismatch: {relative}")
    return manifest


if __name__ == "__main__":
    result = verify(Path(__file__).resolve().parent)
    print(f"Verified {result['base_version']} -> {result['project_version']} payload.")
