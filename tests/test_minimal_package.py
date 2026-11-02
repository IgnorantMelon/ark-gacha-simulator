import hashlib
import shutil
import tarfile
import zipfile

import pytest
from test_deployment import ROOT, load_script


@pytest.fixture
def release_inputs(tmp_path):
    builder = load_script("scripts/build_minimal_package.py")
    root = tmp_path / "release"
    relatives = builder.OVERLAY + [
        "deploy/linux/baseline-0.1.3.toml",
        "deploy/linux/Dockerfile.minimal",
        "scripts/linux/minimal-upgrade.sh",
        "scripts/linux/restore_incremental.py",
        "scripts/linux/check_incremental_base.py",
        "scripts/linux/verify_incremental.py",
    ]
    for relative in relatives:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    shutil.copytree(
        ROOT / "src/ark_gacha",
        root / "src/ark_gacha",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    wheel = tmp_path / "ark_gacha_simulator-0.1.4-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "ark_gacha_simulator-0.1.4.dist-info/METADATA",
            "Version: 0.1.4\nRequires-Dist: regex<2027,>=2026.9.29\n",
        )
        for source in (root / "src").rglob("*"):
            if source.is_file():
                archive.write(source, source.relative_to(root / "src").as_posix())
    # Use a small dependency fixture with its hash explicitly pinned in the fixture lock.
    dependency = tmp_path / builder.dependency_wheel(root)["url"].rsplit("/", 1)[1]
    dependency.write_bytes(b"test-only locked dependency wheel")
    lock = root / "uv.lock"
    content = lock.read_text(encoding="utf-8")
    old_hash = builder.dependency_wheel(root)["hash"].split(":")[1]
    lock.write_text(content.replace(old_hash, hashlib.sha256(dependency.read_bytes()).hexdigest()))
    return builder, root, wheel, dependency


def test_minimal_package_contains_only_two_wheels_and_application_files(tmp_path, release_inputs):
    builder, root, wheel, dependency = release_inputs
    destination = builder.build(root, wheel, dependency, tmp_path / "dist")
    extracted = tmp_path / "extracted"
    with tarfile.open(destination) as archive:
        archive.extractall(extracted, filter="data")
    package = extracted / "ark-gacha-upgrade-0.1.3-to-0.1.4"
    verifier = load_script("scripts/linux/verify_incremental.py")
    manifest = verifier.verify(package)
    assert manifest["base_version"] == "0.1.3"
    assert manifest["project_version"] == "0.1.4"
    assert manifest["added_dependencies"] == ["regex"]
    assert len(list((package / "wheels").glob("*.whl"))) == 2
    for name in manifest["sha256"]:
        assert not set(name.split("/")) & {"data", "seed", "tests", ".env", ".venv"}
        assert not name.endswith((".sqlite3", ".tar", ".tar.gz"))
    assert b"\r" not in (package / "upgrade.sh").read_bytes()
    assert destination.with_name(destination.name + ".sha256").read_bytes().endswith(b"\n")


@pytest.mark.parametrize("change", ["dependency", "source", "lock"])
def test_minimal_builder_rejects_drift(tmp_path, release_inputs, change):
    builder, root, wheel, dependency = release_inputs
    if change == "dependency":
        dependency.write_bytes(b"tampered dependency")
    elif change == "source":
        (root / "src/ark_gacha/bot.py").write_text("# different from wheel")
    else:
        lock = root / "uv.lock"
        lock.write_text(lock.read_text().replace('version = "0.22.1"', 'version = "0.22.2"'))
    with pytest.raises(ValueError, match="locked|stale"):
        builder.build(root, wheel, dependency, tmp_path / "dist")


def test_rollback_restores_old_files_removes_new_code_and_preserves_data(tmp_path):
    helper = load_script("scripts/linux/restore_incremental.py")
    target, backup = tmp_path / "deployment", tmp_path / "backup"
    target.mkdir()
    backup.mkdir()
    (target / "old.py").write_text("old code")
    with tarfile.open(backup / "code.tar.gz", "w:gz") as archive:
        archive.add(target / "old.py", arcname="old.py")
    (backup / "added-files.txt").write_text("new.py\n")
    (target / "old.py").write_text("new code")
    (target / "new.py").write_text("added code")
    (target / "data").mkdir()
    (target / "data/rules.toml").write_text("personal rules")
    helper.restore(target, backup)
    assert (target / "old.py").read_text() == "old code"
    assert not (target / "new.py").exists()
    assert (target / "data/rules.toml").read_text() == "personal rules"


def test_rollback_rejects_paths_outside_deployment(tmp_path):
    helper = load_script("scripts/linux/restore_incremental.py")
    (tmp_path / "added-files.txt").write_text("../outside.py\n")
    with pytest.raises(ValueError, match="Invalid rollback path"):
        helper.restore(tmp_path / "deployment", tmp_path)
