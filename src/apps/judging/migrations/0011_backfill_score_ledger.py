"""Give reviews submitted before the ledger existed their ledger entries.

Upgraded deployments already hold submitted reviews. Without entries, the
ledger check would report every one of them. This appends one "backfilled"
entry per submitted review, per event, in submission order, using the same
payload and hashing as ``apps.judging.ledger.append``. Fresh installs have no
reviews at this point, so it does nothing there.
"""

import hashlib

from django.db import migrations

from core import signing


def backfill(apps, schema_editor):
    Review = apps.get_model("judging", "Review")
    ScoreItem = apps.get_model("judging", "ScoreItem")
    ScoreEvent = apps.get_model("judging", "ScoreEvent")

    covered = set(ScoreEvent.objects.values_list("review_id", flat=True))
    pending = (
        Review.objects.filter(status="submitted")
        .exclude(pk__in=covered)
        .order_by("event_id", "submitted_at", "id")
    )
    for review in pending:
        last = ScoreEvent.objects.filter(event_id=review.event_id).order_by("-seq").first()
        seq = last.seq + 1 if last else 1
        prev_hash = last.entry_hash if last else signing.GENESIS_HASH
        scores = {
            item.criterion.key: int(item.value)
            for item in ScoreItem.objects.filter(review=review).select_related("criterion")
        }
        payload = {
            "event": review.event_id,
            "seq": seq,
            "kind": "backfilled",
            "review": review.pk,
            "judge": review.judge_id,
            "project": review.project_id,
            "version": review.version_id,
            "scores": dict(sorted(scores.items())),
            "feedback_sha256": hashlib.sha256(
                f"{review.comment}\n{review.improvement}".encode()
            ).hexdigest(),
            "at": (review.submitted_at or review.started_at).isoformat(),
        }
        payload_hash = signing.payload_hash(payload)
        entry_hash = signing.chain_hash(prev_hash, payload_hash)
        signature = signing.sign({"entry_hash": entry_hash})
        ScoreEvent.objects.create(
            event_id=review.event_id,
            seq=seq,
            review_id=review.pk,
            payload=payload,
            payload_hash=payload_hash,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
            signature=signature.value,
            key_id=signature.key_id,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("judging", "0010_score_ledger_append_only"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
