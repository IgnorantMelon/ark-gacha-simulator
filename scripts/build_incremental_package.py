"""Build the small, offline 0.1.2 -> 0.1.3 Linux application upgrade."""

import argparse
import hashlib
import io
import json
import tarfile
import tomllib
import zipfile
from datetime import UTC, datetime
from pathlib import Path

BASE_VERSION = "0.1.2"
TARGET_VERSION = "0.1.3"
OVERLAY = [
    "pyproject.toml",
    "uv.lock",
    "compose.yaml",
    "README.md",
    "CHANGELOG.md",
    "src/ark_gacha/bot.py",
    "src/ark_gacha/cli.py",
    "src/ark_gacha/render.py",
    "src/ark_gacha/assets/unknown-operator-180.png",
    "scripts/build_linux_package.py",
    "docs/linux-deploy.md",
    "docs/incremental-upgrade.md",
    "deploy/linux/VERIFIED.md",
]


def lock_fingerprint(content: bytes) -> str:
    lock = tomllib.loads(content.decode("utf-8"))
    for package in lock["package"]:
        if package["name"] == "ark-gacha-simulator":
            package.pop("version", None)
    return hashlib.sha256(json.dumps(lock, sort_keys=True).encode()).hexdigest()


def build(root: Path, wheel: Path, output: Path) -> Path:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    baseline = tomllib.loads(
        (root / f"deploy/linux/baseline-{BASE_VERSION}.toml").read_text(encoding="utf-8")
    )
    version = project["project"]["version"]
    if version != TARGET_VERSION:
        raise ValueError(f"This incremental builder targets {TARGET_VERSION}")
    expected = baseline["project"] | {"version": version}
    if project["project"] != expected:
        raise ValueError("Offline incremental upgrades cannot change project dependencies/metadata")
    if project["build-system"] != baseline["build-system"]:
        raise ValueError("Offline incremental upgrades cannot change the build backend")
    if (
        lock_fingerprint((root / "uv.lock").read_bytes())
        != baseline["baseline"]["lock_fingerprint"]
    ):
        raise ValueError("Offline incremental upgrades cannot change locked dependencies")
    with zipfile.ZipFile(wheel) as archive:
        metadata = archive.read(f"ark_gacha_simulator-{version}.dist-info/METADATA").decode()
        if f"Version: {version}\n" not in metadata:
            raise ValueError("Wheel version does not match the project")
        for relative in (name for name in OVERLAY if name.startswith("src/")):
            if archive.read(relative.removeprefix("src/")) != (root / relative).read_bytes():
                raise ValueError(f"Wheel is stale: {relative}")
    files = {
        "Dockerfile": (root / "deploy/linux/Dockerfile.incremental").read_bytes(),
        "upgrade.sh": (root / "scripts/linux/incremental-upgrade.sh").read_bytes(),
        "verify.py": (root / "scripts/linux/verify_incremental.py").read_bytes(),
        "README.md": (root / "docs/incremental-upgrade.md").read_bytes(),
        f"wheels/{wheel.name}": wheel.read_bytes(),
    }
    files.update({f"overlay/{name}": (root / name).read_bytes() for name in OVERLAY})
    files = {
        name: body if name.endswith((".whl", ".png")) else body.replace(b"\r\n", b"\n")
        for name, body in files.items()
    }
    manifest = {
        "base_version": BASE_VERSION,
        "project_version": version,
        "base_archive_sha256": baseline["baseline"]["archive_sha256"],
        "built_at": datetime.now(UTC).isoformat(),
        "sha256": {name: hashlib.sha256(body).hexdigest() for name, body in sorted(files.items())},
    }
    files["MANIFEST.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    name = f"ark-gacha-upgrade-{BASE_VERSION}-to-{version}"
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"{name}-linux.tar.gz"
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
    parser.add_argument("--output", type=Path, default=root / "dist")
    args = parser.parse_args()
    print(build(root, args.wheel, args.output).resolve())


if __name__ == "__main__":
    main()
