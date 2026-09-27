from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.ops.backups import BackupError, create_backup


class Command(BaseCommand):
    help = "Write a consistent backup of the database, uploads and keys. Safe while running."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--output", type=Path, help="Where to write the archive.")
        parser.add_argument(
            "--keep", type=int, help="Keep only the newest N backups in the backups folder."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["keep"] is not None and options["keep"] < 1:
            raise CommandError("--keep must be at least 1")
        try:
            result = create_backup(options["output"], keep=options["keep"])
        except BackupError as exc:
            raise CommandError(str(exc)) from exc
        files = len(result.manifest["files"])
        self.stdout.write(f"backup written: {result.path} ({result.size:,} bytes, {files} files)")
        self.stdout.write("It holds the secret key and the signing key: store it like a password.")
