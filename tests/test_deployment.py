import hashlib
import importlib.util
import json
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_script(relative):
    spec = importlib.util.spec_from_file_location("deployment_helper", ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_linux_initialization_generates_private_config_and_preserves_existing_data(tmp_path):
    configure = load_script("scripts/linux/configure.py")
    (tmp_path / ".env.example").write_text("ARK_ONEBOT_TOKEN=\nARK_DATA_DIR=data\n")
    seed = tmp_path / "seed"
    (seed / "catalog" / "versions").mkdir(parents=True)
    (seed / "catalog" / "current").write_text("0123456789abcdef\n")
    (seed / "catalog" / "versions" / "0123456789abcdef.json").write_text("{}")
    (seed / "avatars").mkdir()
    (seed / "avatars" / "char_fixture.png").write_bytes(b"public-image")
    # Personal runtime state must not be imported, even if put next to seed data.
    (seed / "bot.sqlite3").write_bytes(b"private-user-data")
    result = configure.initialize(tmp_path)
    assert result["config_created"] and result["catalog_seeded"]
    config = (tmp_path / ".env").read_text()
    assert "ARK_ONEBOT_TOKEN=\n" not in config
    token = config.split("ARK_ONEBOT_TOKEN=", 1)[1].splitlines()[0]
    assert len(token) == 64 and set(token) <= set("0123456789abcdef")
    assert not (tmp_path / "data" / "bot.sqlite3").exists()
    assert (tmp_path / "data" / "avatars" / "char_fixture.png").read_bytes() == b"public-image"
    (tmp_path / "data" / "catalog" / "current").write_text("newer-user-version")
    (tmp_path / "data" / "bot.sqlite3").write_bytes(b"existing-db")
    result = configure.initialize(tmp_path)
    assert not result["config_created"] and not result["catalog_seeded"]
    assert (tmp_path / ".env").read_text() == config
    assert (tmp_path / "data" / "catalog" / "current").read_text() == "newer-user-version"
    assert (tmp_path / "data" / "bot.sqlite3").read_bytes() == b"existing-db"


def test_linux_archive_has_verified_manifest_posix_scripts_and_no_private_files(tmp_path):
    builder = load_script("scripts/build_linux_package.py")
    data = tmp_path / "data"
    version = "0123456789abcdef"
    sources = data / "catalog" / "sources" / version
    sources.mkdir(parents=True)
    metadata = {}
    for key in ["client", "details", "operators"]:
        body = b'{"public":"data"}'
        (sources / f"{key}.json").write_bytes(body)
        metadata[key] = {"sha256": hashlib.sha256(body).hexdigest()}
    versions = data / "catalog" / "versions"
    versions.mkdir()
    (data / "catalog" / "current").write_text(version)
    (versions / f"{version}.json").write_text(
        json.dumps(
            {
                "version": version,
                "sources": metadata,
                "pools": [{"name": "Named event", "roster": {"6": ["char_fixture"]}}],
            }
        )
    )
    (data / "avatars").mkdir()
    (data / "avatars" / "char_fixture.png").write_bytes(b"public-image")
    (data / "bot.sqlite3").write_bytes(b"private-user-data")
    archive = builder.build(ROOT, data, tmp_path / "release")
    prefix = "ark-gacha-simulator/"
    with tarfile.open(archive) as tar:
        names = tar.getnames()
        assert prefix + ".env" not in names
        assert not any("sqlite" in n or ".venv/" in n or "__pycache__/" in n for n in names)
        manifest = json.load(tar.extractfile(prefix + "MANIFEST.json"))
        for relative, digest in manifest["sha256"].items():
            content = tar.extractfile(prefix + relative).read()
            assert hashlib.sha256(content).hexdigest() == digest
        shell = tar.getmember(prefix + "scripts/linux/start.sh")
        assert shell.mode == 0o755
        assert b"\r\n" not in tar.extractfile(shell).read()
        assert prefix + "README-LINUX.md" in names
        assert prefix + "compose.yaml" in names
    expected = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert archive.with_name(archive.name + ".sha256").read_text().startswith(expected)
