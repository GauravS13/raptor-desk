import io
import json
import tarfile
import tomllib
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import User
from apps.ops.backups import BackupError, create_backup, list_backups, restore_backup
from core import signing
from core.models import AuditEvent
from core.version import VERSION


@pytest.fixture(autouse=True)
def data_dir(ops_db, settings, tmp_path: Path) -> Path:
    settings.DATA_DIR = tmp_path
    signing.signing_key.cache_clear()
    signing.signing_key()  # creates keys/ed25519.seed
    (tmp_path / "secret_key").write_text("original-secret", encoding="utf-8")
    (tmp_path / "media" / "thumbs").mkdir(parents=True)
    (tmp_path / "media" / "thumbs" / "a.png").write_bytes(b"\x89PNG original")
    yield tmp_path
    signing.signing_key.cache_clear()


def _rewrite(source: Path, target: Path, change) -> None:
    """Copy an archive, letting ``change(name, data)`` alter or add members."""
    with tarfile.open(source, "r:gz") as src, tarfile.open(target, "w:gz") as dst:
        members = [(m.name, src.extractfile(m).read()) for m in src.getmembers()]
        for name, data in change(members):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            dst.addfile(info, io.BytesIO(data))


def test_version_matches_pyproject() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == VERSION


def test_backup_holds_database_keys_and_uploads_with_checksums(data_dir: Path) -> None:
    User.objects.create_user("kept@example.org", "pw-123456789")
    result = create_backup()
    assert result.path.parent == data_dir / "backups"
    files = result.manifest["files"]
    assert {"db.sqlite3", "secret_key", "keys/ed25519.seed", "media/thumbs/a.png"} <= set(files)
    assert result.manifest["version"] == VERSION
    with tarfile.open(result.path) as tar:
        assert json.loads(tar.extractfile("manifest.json").read())["files"] == files
    assert AuditEvent.objects.filter(action="ops.backup_created").exists()


def test_restore_round_trip_with_safety_backup(data_dir: Path) -> None:
    User.objects.create_user("before@example.org", "pw-123456789")
    backup = create_backup().path
    User.objects.create_user("after@example.org", "pw-123456789")
    (data_dir / "media" / "thumbs" / "a.png").write_bytes(b"changed")
    (data_dir / "secret_key").write_text("changed-secret", encoding="utf-8")

    result = restore_backup(backup)

    assert User.objects.filter(email="before@example.org").exists()
    assert not User.objects.filter(email="after@example.org").exists()
    assert (data_dir / "media" / "thumbs" / "a.png").read_bytes() == b"\x89PNG original"
    assert (data_dir / "secret_key").read_text(encoding="utf-8") == "original-secret"
    assert result.safety_backup is not None and result.safety_backup.name.endswith(
        "-pre-restore.tar.gz"
    )
    restored = AuditEvent.objects.get(action="ops.backup_restored")
    assert backup.name in restored.summary


def test_a_damaged_backup_is_refused_before_anything_changes(data_dir: Path) -> None:
    backup = create_backup().path
    User.objects.create_user("current@example.org", "pw-123456789")
    damaged = data_dir / "damaged.tar.gz"
    _rewrite(
        backup,
        damaged,
        lambda ms: [(n, d + b"x" if n == "db.sqlite3" else d) for n, d in ms],
    )
    with pytest.raises(BackupError, match=r"Checksum mismatch for db\.sqlite3"):
        restore_backup(damaged)
    assert User.objects.filter(email="current@example.org").exists()
    assert not [p for p in list_backups() if "pre-restore" in p.name]


@pytest.mark.parametrize("name", ["../escape", "/etc/passwd", "media/../../x", "other.txt"])
def test_unexpected_paths_are_refused(data_dir: Path, name: str) -> None:
    backup = create_backup().path
    evil = data_dir / "evil.tar.gz"
    _rewrite(backup, evil, lambda ms: [*ms, (name, b"boom")])
    with pytest.raises(BackupError, match="Unexpected entry"):
        restore_backup(evil)


def test_a_backup_from_a_newer_version_is_refused(data_dir: Path) -> None:
    backup = create_backup().path
    newer = data_dir / "newer.tar.gz"

    def add_migration(members):
        for name, data in members:
            if name == "manifest.json":
                manifest = json.loads(data)
                manifest["migrations"].append(["judging", "9999_from_the_future"])
                data = json.dumps(manifest).encode()
            yield name, data

    _rewrite(backup, newer, add_migration)
    with pytest.raises(BackupError, match="newer Raptor Desk"):
        restore_backup(newer)


def test_keep_prunes_old_backups(data_dir: Path) -> None:
    for _ in range(4):
        create_backup(keep=2)
    assert len(list_backups()) == 2


def test_restore_command_only_checks_without_yes(data_dir: Path) -> None:
    out = io.StringIO()
    call_command("backup", stdout=out)
    name = list_backups()[-1].name
    assert "backup written" in out.getvalue()
    with pytest.raises(CommandError, match="Nothing restored"):
        call_command("restore", name, stdout=io.StringIO())
    with pytest.raises(CommandError, match="No backup file"):
        call_command("restore", "missing.tar.gz", stdout=io.StringIO())
