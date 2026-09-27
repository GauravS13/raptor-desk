from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.events.models import Event
from apps.seed import demo, fixtures, hygiene


class Command(BaseCommand):
    help = "Load the official fixtures and demo data (demo profile). Safe to run repeatedly."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--fixtures",
            default=str(Path(settings.REPO_DIR) / "data" / "fixtures.json"),
            help="Path to the DOGFOOD fixtures.json file.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if settings.PROFILE != "demo":
            self.stdout.write("production profile: no fixtures, demo accounts or seed tokens.")
            return
        path = Path(options["fixtures"])
        event_id = self._fixture_event_id(path)
        event = Event.objects.filter(pk=event_id).first()
        if event is None:
            result = fixtures.load(path)
            event = result.event
            self.stdout.write(
                "imported fixtures: "
                + ", ".join(f"{count} {name}" for name, count in result.counts.items())
            )
            for renamed in result.renamed_teams:
                self.stdout.write(
                    f"  team name shared by different teams: '{renamed['from']}' "
                    f"({renamed['team']}) shown as '{renamed['to']}'"
                )
        else:
            self.stdout.write(f"fixtures already loaded ({event.pk}); nothing to import.")

        logins = demo.ensure_demo_accounts(event)
        demo.ensure_dogfood_event()

        self.stdout.write("")
        self.stdout.write("seeded. test logins:")
        for login in logins:
            self.stdout.write(f"  {login.role:<12} Authorization: Bearer {login.token}")
        self.stdout.write("")
        self.stdout.write(f"demo sign-in (password {demo.DEMO_PASSWORD}):")
        self.stdout.write(f"  admin        {demo.ADMIN_EMAIL}")
        for login in logins:
            self.stdout.write(f"  {login.role:<12} {login.email}  ({login.note})")
        self.stdout.write("")
        self.stdout.write(f"data hygiene report for {event.name} ({event.pk}):")
        for finding in hygiene.findings(event):
            self.stdout.write(f"  [{finding.code}] {finding.message}")

    @staticmethod
    def _fixture_event_id(path: Path) -> str:
        import json

        return json.loads(path.read_text(encoding="utf-8"))["event"]["id"]
