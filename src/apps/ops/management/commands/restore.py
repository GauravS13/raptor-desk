from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.ops.backups import BackupError, resolve, restore_backup, verify_backup


class Command(BaseCommand):
    help = "Check a backup, then replace the current data with it. Stop the app and worker first."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("file", help="Backup path, or a file name in the backups folder.")
        parser.add_argument(
            "--yes", action="store_true", help="Replace the current data. Without it, only check."
        )
        parser.add_argument(
            "--no-safety-backup",
            action="store_true",
            help="Skip the automatic backup of the current state.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            path = resolve(options["file"])
            manifest = verify_backup(path)
        except BackupError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"{path.name}: verified. Raptor Desk {manifest['version']}, made "
            f"{manifest['created_at']}, {len(manifest['files'])} files, all checksums match."
        )
        if not options["yes"]:
            raise CommandError(
                "Nothing restored. Stop the app and worker, then re-run with --yes "
                "to replace the current data."
            )
        try:
            result = restore_backup(path, safety_backup=not options["no_safety_backup"])
        except BackupError as exc:
            raise CommandError(str(exc)) from exc
        if result.safety_backup:
            self.stdout.write(f"previous state saved first: {result.safety_backup}")
        self.stdout.write("restored. Start the portal again; it applies any newer migrations.")
