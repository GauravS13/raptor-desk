"""Backups an organizing team can trust, and restores that check before they touch anything.

A backup is one ``.tar.gz`` holding:

- a consistent copy of the database, taken with SQLite's online backup API,
  so the portal keeps serving while it runs;
- uploaded files, the secret key and the signing key, so restored signatures
  still verify;
- ``manifest.json`` with the app version, the applied migrations and a
  SHA-256 for every file.

Restore verifies every hash, refuses unexpected paths and backups from a newer
version, and takes a safety backup of the current state before replacing it.
"""

import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from django.conf import settings
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder

from core import audit, clock, signing
from core.version import VERSION

FORMAT = 1
PREFIX = "raptor-desk-"
SUFFIX = ".tar.gz"
_MEMBER = re.compile(r"^(manifest\.json|db\.sqlite3|secret_key|keys/[^/]+|media/.+)$")


class BackupError(Exception):
    """The backup cannot be made or used. Nothing has been changed."""


@dataclass(frozen=True)
class BackupResult:
    path: Path
    size: int
    manifest: dict[str, Any]


def backups_dir() -> Path:
    return Path(settings.DATA_DIR) / "backups"


def list_backups() -> list[Path]:
    """Backups in the data volume, oldest first (names sort by time)."""
    folder = backups_dir()
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob(f"{PREFIX}*{SUFFIX}") if p.is_file())


def resolve(name: str) -> Path:
    """Accept a path, or a bare file name from the backups folder."""
    path = Path(name)
    if not path.exists() and (backups_dir() / name).exists():
        path = backups_dir() / name
    if not path.is_file():
        raise BackupError(f"No backup file at {name}")
    return path


def _require_sqlite() -> None:
    if connection.vendor != "sqlite":
        raise BackupError(
            "Built-in backups cover the default SQLite database. "
            "For PostgreSQL, use pg_dump and copy the data volume's keys/ and media/ folders."
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def _snapshot_database(target: Path) -> None:
    connection.ensure_connection()
    copy = sqlite3.connect(target)
    try:
        connection.connection.backup(copy)
        # One self-contained file in the archive, with no -wal companion.
        copy.execute("PRAGMA journal_mode=DELETE")
    finally:
        copy.close()


def _data_files() -> list[tuple[str, Path]]:
    data = Path(settings.DATA_DIR)
    found: list[tuple[str, Path]] = []
    secret = data / "secret_key"
    if secret.is_file():
        found.append(("secret_key", secret))
    for folder in ("keys", "media"):
        root = data / folder
        if root.is_dir():
            found += [
                (f.relative_to(data).as_posix(), f) for f in sorted(root.rglob("*")) if f.is_file()
            ]
    return found


def _new_name(label: str) -> Path:
    stamp = clock.now().strftime("%Y%m%d-%H%M%S")
    tail = f"-{label}" if label else ""
    candidate = backups_dir() / f"{PREFIX}{stamp}{tail}{SUFFIX}"
    n = 2
    while candidate.exists():
        candidate = backups_dir() / f"{PREFIX}{stamp}{tail}-{n}{SUFFIX}"
        n += 1
    return candidate


def create_backup(
    output: Path | None = None, *, keep: int | None = None, label: str = ""
) -> BackupResult:
    """Write a verified backup archive and return where it went."""
    _require_sqlite()
    backups_dir().mkdir(parents=True, exist_ok=True)
    target = output or _new_name(label)
    with tempfile.TemporaryDirectory(dir=backups_dir()) as tmp:
        database = Path(tmp) / "db.sqlite3"
        _snapshot_database(database)
        members = [("db.sqlite3", database), *_data_files()]
        manifest = {
            "format": FORMAT,
            "app": "raptor-desk",
            "version": VERSION,
            "created_at": clock.now().isoformat(),
            "profile": settings.PROFILE,
            "migrations": sorted(
                [app, name] for app, name in MigrationRecorder(connection).applied_migrations()
            ),
            "files": {
                name: {"sha256": _sha256(path), "bytes": path.stat().st_size}
                for name, path in members
            },
        }
        partial = target.with_name(target.name + ".partial")
        with tarfile.open(partial, "w:gz") as tar:
            body = json.dumps(manifest, indent=2, sort_keys=True).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size, info.mtime, info.mode = len(body), int(clock.now().timestamp()), 0o600
            tar.addfile(info, io.BytesIO(body))
            for name, path in members:
                tar.add(path, arcname=name, recursive=False)
        os.chmod(partial, 0o600)
        os.replace(partial, target)

    if keep:
        older = [p for p in list_backups() if p != target]
        for old in older[: max(len(older) - (keep - 1), 0)]:
            old.unlink()
    size = target.stat().st_size
    audit.record(
        "ops.backup_created",
        f"Backup created: {target.name} ({len(manifest['files'])} files, {size} bytes)",
        details={"file": target.name, "bytes": size},
    )
    return BackupResult(path=target, size=size, manifest=manifest)


def _read_manifest(tar: tarfile.TarFile) -> dict[str, Any]:
    handle = tar.extractfile("manifest.json")
    try:
        manifest = json.loads(handle.read()) if handle else None
        valid = (
            isinstance(manifest, dict)
            and manifest.get("format") == FORMAT
            and manifest.get("app") == "raptor-desk"
            and all(isinstance(f.get("sha256"), str) for f in manifest["files"].values())
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise BackupError("This file is not a Raptor Desk backup in a supported format")
    return manifest  # type: ignore[return-value]


def verify_backup(path: Path) -> dict[str, Any]:
    """Check an archive completely without extracting it. Returns its manifest."""
    try:
        with tarfile.open(path, "r:gz") as tar:
            manifest = _check_archive(tar)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise BackupError(f"{path.name} is not a readable backup archive: {exc}") from exc

    known = set(MigrationLoader(None, ignore_no_migrations=True).disk_migrations)
    unknown = [f"{a}.{n}" for a, n in manifest.get("migrations", []) if (a, n) not in known]
    if unknown:
        raise BackupError(
            f"This backup comes from a newer Raptor Desk ({manifest.get('version')}); "
            f"upgrade first. Unknown migrations: {', '.join(unknown[:5])}"
        )
    return manifest


def _check_archive(tar: tarfile.TarFile) -> dict[str, Any]:
    """Every entry expected, nothing outside the manifest, every checksum right."""
    entries = tar.getmembers()
    for entry in entries:
        parts = PurePosixPath(entry.name).parts
        if not entry.isfile() or ".." in parts or "\\" in entry.name:
            raise BackupError(f"Unexpected entry in backup: {entry.name!r}")
        if not _MEMBER.match(entry.name):
            raise BackupError(f"Unexpected entry in backup: {entry.name!r}")
    names = [e.name for e in entries]
    if len(names) != len(set(names)):
        raise BackupError("The backup lists a file twice")
    if "manifest.json" not in names:
        raise BackupError("The backup has no manifest.json")
    manifest = _read_manifest(tar)
    listed = set(manifest["files"])
    present = set(names) - {"manifest.json"}
    if listed != present or "db.sqlite3" not in present:
        raise BackupError("The files in the backup do not match its manifest")
    for name in sorted(present):
        digest = hashlib.sha256()
        handle = tar.extractfile(name)
        if handle is None:
            raise BackupError(f"Cannot read {name} from the backup")
        for block in iter(lambda h=handle: h.read(1 << 16), b""):
            digest.update(block)
        if digest.hexdigest() != manifest["files"][name]["sha256"]:
            raise BackupError(f"Checksum mismatch for {name}: the backup is damaged")
    return manifest


def _replace_folder(source: Path, target: Path) -> None:
    """Swap a folder in with renames, so a crash leaves either the old or the new one."""
    if not source.exists():
        source.mkdir()
    aside = target.with_name(f".{target.name}-replaced")
    if aside.exists():
        shutil.rmtree(aside)
    if target.exists():
        target.rename(aside)
    shutil.move(str(source), str(target))
    if aside.exists():
        shutil.rmtree(aside)


@dataclass(frozen=True)
class RestoreResult:
    manifest: dict[str, Any]
    safety_backup: Path | None


def restore_backup(path: Path, *, safety_backup: bool = True) -> RestoreResult:
    """Replace the current data with a verified backup. Stop the app and worker first."""
    _require_sqlite()
    manifest = verify_backup(path)
    safety = create_backup(label="pre-restore").path if safety_backup else None
    data = Path(settings.DATA_DIR)

    with tempfile.TemporaryDirectory(dir=backups_dir()) as tmp:
        with tarfile.open(path, "r:gz") as tar:
            tar.extractall(tmp, filter="data")
        unpacked = Path(tmp)

        source = sqlite3.connect(unpacked / "db.sqlite3")
        try:
            connection.ensure_connection()
            source.backup(connection.connection)
        finally:
            source.close()
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA quick_check")
            if cursor.fetchone()[0] != "ok":
                raise BackupError("The restored database failed its integrity check")

        _replace_folder(unpacked / "media", data / "media")
        _replace_folder(unpacked / "keys", data / "keys")
        if (unpacked / "secret_key").is_file():
            os.replace(unpacked / "secret_key", data / "secret_key")
    signing.signing_key.cache_clear()

    audit.record(
        "ops.backup_restored",
        f"Data restored from backup {path.name}, made {manifest['created_at']}",
        details={"file": path.name, "safety_backup": safety.name if safety else ""},
    )
    return RestoreResult(manifest=manifest, safety_backup=safety)
