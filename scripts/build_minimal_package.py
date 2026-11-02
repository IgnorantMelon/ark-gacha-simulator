"""Build the offline Linux amd64 0.1.3 -> 0.1.4 upgrade with only the new dependency."""

import argparse
import hashlib
import io
import json
import tarfile
import tomllib
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

BASE_VERSION = "0.1.3"
TARGET_VERSION = "0.1.4"
OVERLAY = [
    "pyproject.toml",
    "uv.lock",
    "compose.yaml",
    "README.md",
    "CHANGELOG.md",
    "docs/auto-replies.md",
    "docs/linux-deploy.md",
    "docs/minimal-upgrade.md",
    "deploy/linux/VERIFIED.md",
]


def baseline_fingerprint(content: bytes) -> str:
    lock = tomllib.loads(content.decode())
    lock["package"] = [p for p in lock["package"] if p["name"] != "regex"]
    for package in lock["package"]:
        if package["name"] == "ark-gacha-simulator":
            package.pop("version", None)
            package["dependencies"] = [d for d in package["dependencies"] if d["name"] != "regex"]
            package["metadata"]["requires-dist"] = [
                d for d in package["metadata"]["requires-dist"] if d["name"] != "regex"
            ]
    return hashlib.sha256(json.dumps(lock, sort_keys=True).encode()).hexdigest()


def dependency_wheel(root: Path) -> dict:
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    package = next(p for p in lock["package"] if p["name"] == "regex")
    if package.get("dependencies"):
        raise ValueError("The minimal dependency must not introduce transitive dependencies")
    return next(w for w in package["wheels"] if "-cp311-cp311-manylinux2014_x86_64." in w["url"])


def build(root: Path, wheel: Path, dependency: Path, output: Path) -> Path:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    baseline = tomllib.loads(
        (root / "deploy/linux/baseline-0.1.3.toml").read_text(encoding="utf-8")
    )
    expected = baseline["project"] | {
        "version": TARGET_VERSION,
        "dependencies": baseline["project"]["dependencies"] + ["regex>=2026.9.29,<2027"],
    }
    if project["project"] != expected or project["build-system"] != baseline["build-system"]:
        raise ValueError("Only the application version and the approved regex addition may change")
    if (
        baseline_fingerprint((root / "uv.lock").read_bytes())
        != baseline["baseline"]["lock_fingerprint"]
    ):
        raise ValueError("Existing locked dependencies changed; a minimal upgrade is unsafe")
    record = dependency_wheel(root)
    if (
        dependency.name != Path(urlparse(record["url"]).path).name
        or hashlib.sha256(dependency.read_bytes()).hexdigest() != record["hash"].split(":")[1]
    ):
        raise ValueError("Dependency wheel does not match the locked Linux amd64 artifact")
    source_files = {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted((root / "src/ark_gacha").rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }
    with zipfile.ZipFile(wheel) as archive:
        metadata = archive.read(f"ark_gacha_simulator-{TARGET_VERSION}.dist-info/METADATA").decode()
        if f"Version: {TARGET_VERSION}\n" not in metadata or "Requires-Dist: regex" not in metadata:
            raise ValueError("Wheel metadata does not match the release")
        actual = {name for name in archive.namelist() if name.startswith("ark_gacha/")}
        if actual != {name.removeprefix("src/") for name in source_files}:
            raise ValueError("Wheel source inventory differs from the current source")
        for name, body in source_files.items():
            if archive.read(name.removeprefix("src/")) != body:
                raise ValueError(f"Wheel is stale: {name}")
    files = {
        "Dockerfile": (root / "deploy/linux/Dockerfile.minimal").read_bytes(),
        "upgrade.sh": (root / "scripts/linux/minimal-upgrade.sh").read_bytes(),
        "restore-code.py": (root / "scripts/linux/restore_incremental.py").read_bytes(),
        "check-base.py": (root / "scripts/linux/check_incremental_base.py").read_bytes(),
        "verify.py": (root / "scripts/linux/verify_incremental.py").read_bytes(),
        "README.md": (root / "docs/minimal-upgrade.md").read_bytes(),
        f"wheels/{wheel.name}": wheel.read_bytes(),
        f"wheels/{dependency.name}": dependency.read_bytes(),
    }
    files.update({f"overlay/{name}": (root / name).read_bytes() for name in OVERLAY})
    files.update({f"overlay/{name}": body for name, body in source_files.items()})
    files = {
        name: body if name.endswith((".whl", ".png")) else body.replace(b"\r\n", b"\n")
        for name, body in files.items()
    }
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    manifest = {
        "base_version": BASE_VERSION,
        "project_version": TARGET_VERSION,
        "platform": "linux/amd64",
        "python": "3.11",
        "base_archive_sha256": baseline["baseline"]["archive_sha256"],
        "locked_versions": {p["name"]: p["version"] for p in lock["package"]},
        "added_dependencies": ["regex"],
        "built_at": datetime.now(UTC).isoformat(),
        "sha256": {name: hashlib.sha256(body).hexdigest() for name, body in sorted(files.items())},
    }
    files["MANIFEST.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    name = f"ark-gacha-upgrade-{BASE_VERSION}-to-{TARGET_VERSION}"
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"{name}-linux-amd64.tar.gz"
    with tarfile.open(destination, "w:gz") as archive:
        for relative, body in sorted(files.items()):
            info = tarfile.TarInfo(f"{name}/{relative}")
            info.size = len(body)
            info.mode = 0o755 if relative.endswith(".sh") else 0o644
            archive.addfile(info, io.BytesIO(body))
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_name(destination.name + ".sha256").write_text(
        f"{digest}  {destination.name}\n", encoding="utf-8", newline="\n"
    )
    return destination


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--dependency-wheel", type=Path)
    parser.add_argument("--output", type=Path, default=root / "dist")
    args = parser.parse_args()
    dependency = args.dependency_wheel
    if dependency is None:
        record = dependency_wheel(root)
        dependency = root / "output/release-0.1.4/wheels" / Path(urlparse(record["url"]).path).name
        dependency.parent.mkdir(parents=True, exist_ok=True)
        if not dependency.exists():
            with urllib.request.urlopen(record["url"], timeout=60) as response:
                dependency.write_bytes(response.read())
    print(build(root, args.wheel, dependency, args.output).resolve())


if __name__ == "__main__":
    main()
