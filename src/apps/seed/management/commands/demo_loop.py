from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.events.models import Event
from apps.judging import close_calls
from apps.seed import loop_demo


class Command(BaseCommand):
    help = (
        "Demo profile only: run Measure, Doubt, Ask on the fixture event, entering synthetic "
        "reviews labelled [demo], and show how the close calls move."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--event", default="evt_01")
        parser.add_argument("--budget", type=int, default=close_calls.DEFAULT_BUDGET)

    def handle(self, *args: Any, **options: Any) -> None:
        event = Event.objects.filter(pk=options["event"]).first()
        if event is None:
            raise CommandError(f"No event {options['event']}")
        try:
            moves = loop_demo.run(event, budget=options["budget"])
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc
        if not moves:
            self.stdout.write("Nothing to ask: no close call has an eligible judge left.")
            return
        self.stdout.write(
            "chance of finishing inside the cutoff, before and after the asked reviews:"
        )
        for m in moves:
            state = "settled" if m.settled else "still close"
            self.stdout.write(
                f"  {m.project}  top {m.cutoff}  {m.before:>4.0%} -> {m.after:>4.0%}  {state}"
            )
        self.stdout.write(
            "The synthetic reviews are labelled [demo] in their comments and audit trail."
        )
