import hashlib
import json
import shutil
import tarfile
import tomllib
import zipfile

import pytest
from test_deployment import ROOT, load_script


def make_wheel(tmp_path, root=ROOT):
    path = tmp_path / "ark_gacha_simulator-0.1.3-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ark_gacha_simulator-0.1.3.dist-info/METADATA", "Version: 0.1.3\n")
        for filename in ["bot.py", "cli.py", "render.py", "assets/unknown-operator-180.png"]:
            archive.write(root / "src/ark_gacha" / filename, f"ark_gacha/{filename}")
    return path


@pytest.fixture
def compatible_legacy_root(tmp_path):
    """Test archive mechanics with a compatible fixture, independent of new app dependencies."""
    builder = load_script("scripts/build_incremental_package.py")
    root = tmp_path / "legacy"
    for relative in builder.OVERLAY + [
        "deploy/linux/Dockerfile.incremental",
        "scripts/linux/incremental-upgrade.sh",
        "scripts/linux/verify_incremental.py",
    ]:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    baseline = (ROOT / "deploy/linux/baseline-0.1.2.toml").read_text(encoding="utf-8")
    project = baseline.split("[baseline]", 1)[0].replace('version = "0.1.2"', 'version = "0.1.3"')
    (root / "pyproject.toml").write_text(project, encoding="utf-8")
    lock = b'[[package]]\nname = "ark-gacha-simulator"\nversion = "0.1.3"\n'
    (root / "uv.lock").write_bytes(lock)
    original_digest = tomllib.loads(baseline)["baseline"]["lock_fingerprint"]
    (root / "deploy/linux/baseline-0.1.2.toml").write_text(
        baseline.replace(original_digest, builder.lock_fingerprint(lock)), encoding="utf-8"
    )
    return root


def test_incremental_archive_is_small_and_excludes_runtime_and_dependencies(
    tmp_path, compatible_legacy_root
):
    builder = load_script("scripts/build_incremental_package.py")
    root = compatible_legacy_root
    archive_path = builder.build(root, make_wheel(tmp_path, root), tmp_path / "dist")
    assert archive_path.stat().st_size < 100 * 1024
    with tarfile.open(archive_path) as archive:
        prefix = "ark-gacha-upgrade-0.1.2-to-0.1.3/"
        manifest = json.load(archive.extractfile(prefix + "MANIFEST.json"))
        assert manifest["base_version"] == "0.1.2"
        assert manifest["project_version"] == "0.1.3"
        for name, digest in manifest["sha256"].items():
            body = archive.extractfile(prefix + name).read()
            assert hashlib.sha256(body).hexdigest() == digest
            assert not any(part in name.split("/") for part in ["data", "seed", ".env", ".venv"])
            assert not name.startswith("overlay/tests/")
            assert not name.endswith((".tar", ".tar.gz"))
            assert not name.endswith(".sqlite3")
            if name.endswith(".png"):
                assert name == "overlay/src/ark_gacha/assets/unknown-operator-180.png"
                assert body == (ROOT / "src/ark_gacha/assets/unknown-operator-180.png").read_bytes()
        script = archive.getmember(prefix + "upgrade.sh")
        assert script.mode == 0o755
        assert b"\r" not in archive.extractfile(script).read()
    assert archive_path.with_name(archive_path.name + ".sha256").read_bytes() == (
        f"{hashlib.sha256(archive_path.read_bytes()).hexdigest()}  {archive_path.name}\n".encode()
    )


def test_historical_incremental_builder_rejects_new_regex_dependency(tmp_path):
    builder = load_script("scripts/build_incremental_package.py")
    root = tmp_path / "new-dependencies"
    (root / "deploy/linux").mkdir(parents=True)
    shutil.copyfile(
        ROOT / "deploy/linux/baseline-0.1.2.toml", root / "deploy/linux/baseline-0.1.2.toml"
    )
    project = (
        (ROOT / "pyproject.toml").read_text().replace('version = "0.1.4"', 'version = "0.1.3"')
    )
    (root / "pyproject.toml").write_text(project)
    with pytest.raises(ValueError, match="cannot change project dependencies"):
        builder.build(root, make_wheel(tmp_path), tmp_path / "dist")


def test_dependency_fingerprint_ignores_only_the_application_version():
    builder = load_script("scripts/build_incremental_package.py")
    content = (ROOT / "uv.lock").read_bytes()
    previous = content.replace(
        b'name = "ark-gacha-simulator"\nversion = "0.1.4"',
        b'name = "ark-gacha-simulator"\nversion = "0.1.2"',
    )
    assert previous != content
    assert builder.lock_fingerprint(previous) == builder.lock_fingerprint(content)
    changed_dependency = content.replace(b'name = "aiosqlite"', b'name = "aiosqlite-modified"', 1)
    assert builder.lock_fingerprint(changed_dependency) != builder.lock_fingerprint(content)


@pytest.mark.parametrize("extra_file", [False, True])
def test_payload_verification_rejects_tampering_and_extra_files(tmp_path, extra_file):
    verifier = load_script("scripts/linux/verify_incremental.py")
    payload = b"known application"
    (tmp_path / "payload").write_bytes(payload)
    manifest = {"sha256": {"payload": hashlib.sha256(payload).hexdigest()}}
    (tmp_path / "MANIFEST.json").write_text(json.dumps(manifest))
    assert verifier.verify(tmp_path) == manifest
    if extra_file:
        (tmp_path / "unexpected").write_text("changed")
    else:
        (tmp_path / "payload").write_text("changed")
    with pytest.raises(ValueError, match="checksum|Unexpected"):
        verifier.verify(tmp_path)
