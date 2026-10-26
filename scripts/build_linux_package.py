"""Build a source deployment tarball with public catalog/avatar seed files only."""

import argparse
import hashlib
import io
import json
import re
import tarfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path


def collect_files(root: Path, data: Path) -> dict[str, bytes]:
    files = {}
    for relative in [
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        ".env.example",
        ".dockerignore",
        "Dockerfile",
        "compose.yaml",
        "README.md",
        "CHANGELOG.md",
        "docs/rules.md",
        "docs/linux-deploy.md",
        "deploy/linux/ark-gacha.service",
        "deploy/linux/VERIFIED.md",
        "scripts/build_linux_package.py",
    ]:
        files[relative] = (root / relative).read_bytes()
    files["README-LINUX.md"] = files["docs/linux-deploy.md"]
    for directory, pattern in [("src", "*.py"), ("scripts/linux", "*"), ("tests", "*")]:
        for path in sorted((root / directory).rglob(pattern)):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                if path.is_symlink():
                    raise ValueError(f"Refusing symlink in release: {path.name}")
                files[path.relative_to(root).as_posix()] = path.read_bytes()
    version = (data / "catalog" / "current").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9a-f]{16}", version):
        raise ValueError("Invalid seed catalog version")
    snapshot_path = data / "catalog" / "versions" / f"{version}.json"
    snapshot = json.loads(snapshot_path.read_bytes())
    if snapshot["version"] != version or not snapshot["pools"]:
        raise ValueError("Invalid seed catalog snapshot")
    # The deployable catalog must already reflect the latest collection scope.
    if any(
        not p["name"].strip() or p["name"] == "适合多种场合的强力干员" for p in snapshot["pools"]
    ):
        raise ValueError("Sync the named-pools catalog before building a release")
    for suffix in [".json", ".report.json", ".audit.json"]:
        path = data / "catalog" / "versions" / f"{version}{suffix}"
        if path.is_file():
            files[f"seed/catalog/versions/{path.name}"] = path.read_bytes()
    files["seed/catalog/current"] = (version + "\n").encode()
    for key in ["client", "details", "operators"]:
        path = data / "catalog" / "sources" / version / f"{key}.json"
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != snapshot["sources"][key]["sha256"]:
            raise ValueError(f"Source hash mismatch: {key}")
        files[f"seed/catalog/sources/{version}/{key}.json"] = body
    characters = {c for p in snapshot["pools"] for cs in p["roster"].values() for c in cs}
    for char in sorted(characters):
        if not re.fullmatch(r"char_[A-Za-z0-9_]+", char):
            raise ValueError("Invalid avatar ID")
        files[f"seed/avatars/{char}.png"] = (data / "avatars" / f"{char}.png").read_bytes()
    return files


def build(root: Path, data: Path, output: Path) -> Path:
    files = collect_files(root, data)
    for name, body in list(files.items()):
        if name.endswith((".py", ".sh", ".md", ".yaml", ".toml", ".service")) or name in {
            "Dockerfile",
            ".env.example",
            ".dockerignore",
            ".python-version",
            "uv.lock",
        }:
            files[name] = body.replace(b"\r\n", b"\n")
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    manifest = {
        "project_version": version,
        "built_at": datetime.now(UTC).isoformat(),
        "catalog_version": files["seed/catalog/current"].decode().strip(),
        "sha256": {name: hashlib.sha256(body).hexdigest() for name, body in sorted(files.items())},
    }
    files["MANIFEST.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"ark-gacha-simulator-{version}-linux.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, body in sorted(files.items()):
            info = tarfile.TarInfo("ark-gacha-simulator/" + name)
            info.size = len(body)
            info.mode = 0o755 if name.endswith(".sh") else 0o644
            tar.addfile(info, io.BytesIO(body))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )
    return archive


def main():
    parser = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--data", type=Path, default=root / "data")
    parser.add_argument("--output", type=Path, default=root / "dist")
    args = parser.parse_args()
    print(build(root, args.data, args.output).resolve())


if __name__ == "__main__":
    main()
