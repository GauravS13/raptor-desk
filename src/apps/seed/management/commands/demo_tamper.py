from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.events.models import Event
from apps.judging import ledger
from apps.judging.models import ScoreItem
from apps.seed.demo import require_demo_profile


class Command(BaseCommand):
    help = (
        "Demo profile only: change one score directly in the database, the way someone with "
        "database access would, show that the ledger finds it, then put the score back."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--event", default="evt_01")
        parser.add_argument(
            "--keep", action="store_true", help="Leave the tampered score in place."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            require_demo_profile()
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc
        event = Event.objects.filter(pk=options["event"]).first()
        if event is None:
            raise CommandError(f"No event {options['event']}")
        before = ledger.verify(event)
        self.stdout.write(
            f"before: {'valid' if before.valid else 'INVALID'}, {before.entries} entries"
        )

        item = (
            ScoreItem.objects.filter(review__event=event, review__status="submitted")
            .select_related("review", "criterion")
            .order_by("review__submitted_at", "review_id", "criterion__key")
            .first()
        )
        if item is None:
            raise CommandError("No submitted scores to tamper with.")
        original = item.value
        forged = (
            item.criterion.max_score
            if original != item.criterion.max_score
            else (item.criterion.min_score)
        )
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE judging_scoreitem SET value = %s WHERE id = %s", [forged, item.pk]
            )
        review = item.review
        self.stdout.write(
            f"tampered: review {review.pk} ({review.judge_id} on {review.project_id}), "
            f"{item.criterion.key} {original} -> {forged}, by a direct SQL UPDATE"
        )
        after = ledger.verify(event)
        self.stdout.write(
            f"after:  {'valid' if after.valid else 'INVALID'} at entry {after.first_bad_seq}: "
            f"{after.problem}"
        )

        if not options["keep"]:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE judging_scoreitem SET value = %s WHERE id = %s", [original, item.pk]
                )
            restored = ledger.verify(event)
            self.stdout.write(
                f"restored: {'valid' if restored.valid else 'INVALID'}, {restored.entries} entries"
            )
