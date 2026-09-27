import json
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.ops import doctor
from core import clock
from core.version import VERSION


class Command(BaseCommand):
    help = "Check the running portal and say what needs attention. Exits 1 if anything failed."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--json", action="store_true", help="Machine-readable output.")

    def handle(self, *args: Any, **options: Any) -> None:
        findings = doctor.run()
        failures = [f for f in findings if f.status == doctor.FAIL]
        warnings = [f for f in findings if f.status == doctor.WARN]
        if options["json"]:
            self.stdout.write(json.dumps([f.as_dict() for f in findings], indent=2))
        else:
            self.stdout.write(
                f"raptor-desk {VERSION} doctor · {clock.now():%Y-%m-%d %H:%M} UTC · "
                f"profile {settings.PROFILE}\n"
            )
            for f in findings:
                self.stdout.write(f"  {f.status:<5} {f.name:<16} {f.detail}")
                if f.fix and f.status in (doctor.WARN, doctor.FAIL):
                    self.stdout.write(f"  {'':<5} {'':<16} fix: {f.fix}")
            self.stdout.write(f"\n{len(failures)} failed, {len(warnings)} need attention")
        if failures:
            raise CommandError(f"{len(failures)} check(s) failed")
