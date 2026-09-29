"""The score ledger: every submitted score, hash-chained and signed.

Each time a review is submitted, amended or imported, one entry is appended
with its sequence number, the review, judge, project and version, the scores,
a SHA-256 of the written feedback, and the time. Then:

    payload_hash = sha256(canonical JSON of the payload)
    entry_hash   = sha256(previous entry_hash + ":" + payload_hash)
    signature    = Ed25519 signature over {"entry_hash": entry_hash}

The table is append-only in the database. :func:`verify` walks the chain,
recomputes every hash and signature, and then checks that the live scores
still match the latest entry for each review. An edit made directly in the
database, to the ledger or to a score, is found and located by sequence
number.
"""

import hashlib
from dataclasses import dataclass
from typing import Any

from django.db.models import Max

from apps.events.models import Event
from apps.judging.models import Review, ScoreEvent
from core import signing


def feedback_hash(review: Review) -> str:
    text = f"{review.comment}\n{review.improvement}"
    return hashlib.sha256(text.encode()).hexdigest()


def _scores(review: Review) -> dict[str, int]:
    return {
        item.criterion.key: int(item.value)
        for item in review.scores.select_related("criterion").all()
    }


def append(review: Review, kind: str) -> ScoreEvent:
    """Add an entry for ``review``. Call inside the transaction that saved it."""
    event_id = review.event_id
    last = ScoreEvent.objects.filter(event_id=event_id).order_by("-seq").first()
    seq = last.seq + 1 if last else 1
    prev_hash = last.entry_hash if last else signing.GENESIS_HASH
    payload: dict[str, Any] = {
        "event": event_id,
        "seq": seq,
        "kind": kind,
        "review": review.pk,
        "judge": review.judge_id,
        "project": review.project_id,
        "version": review.version_id,
        "scores": dict(sorted(_scores(review).items())),
        "feedback_sha256": feedback_hash(review),
        "at": (review.submitted_at or review.started_at).isoformat(),
    }
    payload_hash = signing.payload_hash(payload)
    entry_hash = signing.chain_hash(prev_hash, payload_hash)
    signature = signing.sign({"entry_hash": entry_hash})
    return ScoreEvent.objects.create(
        event_id=event_id,
        seq=seq,
        review_id=review.pk,
        payload=payload,
        payload_hash=payload_hash,
        prev_hash=prev_hash,
        entry_hash=entry_hash,
        signature=signature.value,
        key_id=signature.key_id,
    )


def append_comparison(comparison: Any) -> ScoreEvent:
    """Add an entry for a pairwise comparison, chained and signed like any other."""
    last = ScoreEvent.objects.filter(event_id=comparison.event_id).order_by("-seq").first()
    seq = last.seq + 1 if last else 1
    prev_hash = last.entry_hash if last else signing.GENESIS_HASH
    payload: dict[str, Any] = {
        "event": comparison.event_id,
        "seq": seq,
        "kind": "pairwise",
        "comparison": comparison.pk,
        "judge": comparison.judge_id,
        "pair": [comparison.project_a_id, comparison.project_b_id],
        "outcome": comparison.outcome,
        "at": comparison.created_at.isoformat(),
    }
    payload_hash = signing.payload_hash(payload)
    entry_hash = signing.chain_hash(prev_hash, payload_hash)
    signature = signing.sign({"entry_hash": entry_hash})
    return ScoreEvent.objects.create(
        event_id=comparison.event_id,
        seq=seq,
        review_id=comparison.pk,
        payload=payload,
        payload_hash=payload_hash,
        prev_hash=prev_hash,
        entry_hash=entry_hash,
        signature=signature.value,
        key_id=signature.key_id,
    )


@dataclass(frozen=True)
class Verification:
    valid: bool
    entries: int
    head: str
    first_bad_seq: int | None = None
    problem: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "entries": self.entries,
            "head": self.head,
            "first_bad_seq": self.first_bad_seq,
            "problem": self.problem,
        }


def head(event: Event) -> tuple[int, str]:
    last = ScoreEvent.objects.filter(event=event).order_by("-seq").first()
    return (last.seq, last.entry_hash) if last else (0, signing.GENESIS_HASH)


def verify(event: Event) -> Verification:
    entries = list(ScoreEvent.objects.filter(event=event).order_by("seq"))
    count = len(entries)
    key_id = signing.key_id()
    prev = signing.GENESIS_HASH
    latest: dict[str, ScoreEvent] = {}

    def bad(seq: int | None, problem: str) -> Verification:
        return Verification(False, count, prev, seq, problem)

    for expected, entry in enumerate(entries, start=1):
        if entry.seq != expected:
            return bad(expected, f"entry {expected} is missing")
        if signing.payload_hash(entry.payload) != entry.payload_hash:
            return bad(entry.seq, "the entry's content was changed after it was written")
        if entry.prev_hash != prev or signing.chain_hash(prev, entry.payload_hash) != (
            entry.entry_hash
        ):
            return bad(entry.seq, "the chain is broken: an earlier entry was changed or removed")
        if entry.key_id != key_id or not signing.verify(
            {"entry_hash": entry.entry_hash}, entry.signature
        ):
            return bad(entry.seq, "the signature does not match this deployment's key")
        prev = entry.entry_hash
        latest[entry.review_id] = entry

    mismatches: list[tuple[int, str]] = []
    reviews = Review.objects.filter(event=event, status="submitted").prefetch_related(
        "scores__criterion"
    )
    for review in reviews:
        entry = latest.get(review.pk)
        if entry is None:
            mismatches.append((count + 1, f"review {review.pk} was submitted without an entry"))
            continue
        if _scores(review) != entry.payload["scores"]:
            mismatches.append(
                (entry.seq, f"the scores of review {review.pk} were changed outside the portal")
            )
        elif feedback_hash(review) != entry.payload["feedback_sha256"]:
            mismatches.append(
                (entry.seq, f"the feedback of review {review.pk} was changed outside the portal")
            )
    if mismatches:
        seq, problem = min(mismatches)
        return Verification(False, count, prev, seq, problem)
    return Verification(True, count, prev)


def export(event: Event) -> dict[str, Any]:
    """Every entry, for checking offline with tools/verify.py."""
    return {
        "kind": "raptor-desk/score-ledger",
        "event": event.pk,
        "public_key": signing.public_key_hex(),
        "entries": [
            {
                "seq": e.seq,
                "payload": e.payload,
                "payload_hash": e.payload_hash,
                "prev_hash": e.prev_hash,
                "entry_hash": e.entry_hash,
                "signature": e.signature,
            }
            for e in ScoreEvent.objects.filter(event=event).order_by("seq")
        ],
    }


def count(event: Event) -> int:
    return ScoreEvent.objects.filter(event=event).aggregate(n=Max("seq"))["n"] or 0
