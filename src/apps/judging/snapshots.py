"""Freeze results into signed, immutable snapshots.

A snapshot is the exact document the organizer publishes: the final order
(computed ranking with deliberation decisions applied), each project's
corrected score, interval and chances, the decisions with their reasons, the
method and parameters, and ``input_hash``, a hash over every review score
that went in. It is serialised as canonical JSON and signed with the
deployment's Ed25519 key (``core.signing``). The table is append-only.

A snapshot is *current* while no review score or decision has changed since
it was taken; publishing needs a current snapshot.
"""

from typing import Any

from django.db import transaction
from django.db.models import Max

from apps.events.models import Event, Phase
from apps.judging import deliberation, ledger
from apps.judging.models import RankingDecision, ResultsSnapshot
from apps.judging.results import EventResults, compute
from core import audit, clock, signing
from core.http import conflict
from core.policy import Principal

KIND = "raptor-desk/results-snapshot"
FORMAT = 1
FREEZE_PHASES = (Phase.DELIBERATION,)


def _r(value: float) -> float:
    return round(float(value), 6)


def input_hash(results: EventResults) -> str:
    """Hash over every observation (judge, project, composite, per-criterion scores)."""
    rows = sorted(
        [
            o.judge,
            o.project,
            _r(o.score),
            [None if v != v else _r(v) for v in (o.vector or ())],  # NaN -> null
        ]
        for o in results.observations
    )
    return signing.payload_hash(rows)


def _decisions(event: Event) -> list[dict[str, Any]]:
    return [
        {
            "id": d.pk,
            "kind": d.kind,
            "project": d.project_id,
            "other": d.other_id,
            "rationale": d.rationale,
            "at": d.created_at.isoformat(),
        }
        for d in RankingDecision.objects.filter(event=event)
    ]


def build_payload(event: Event, number: int) -> dict[str, Any]:
    results = compute(event)
    found = deliberation.board(event, results=results)
    evaluation = results.evaluation
    return {
        "kind": KIND,
        "format": FORMAT,
        "event": {"id": event.pk, "name": event.name},
        "number": number,
        "created_at": clock.now().isoformat(),
        "method": results.method,
        "params": {k: _r(v) for k, v in evaluation.params.items()},
        "cutoffs": list(evaluation.cutoffs),
        "prize_places": found.prize_places,
        "input_hash": input_hash(results),
        "ledger": dict(zip(("entries", "head"), ledger.head(event), strict=True)),
        "reviews": len(results.observations),
        "judges": len({o.judge for o in results.observations}),
        "flags": list(evaluation.flags),
        "gated_out": list(results.gated_out),
        "decisions": _decisions(event),
        "ranking": [
            {
                "place": row.final_rank,
                "computed_place": row.computed_rank,
                "project": row.project_id,
                "name": row.name,
                "team": row.team,
                "score": _r(row.score),
                "ci": [_r(row.ci_low), _r(row.ci_high)],
                "p_top": {str(k): _r(v) for k, v in row.p_top.items()},
                "bonus": _r(row.bonus),
                "flags": list(row.flags),
            }
            for row in found.rows
        ],
    }


@transaction.atomic
def freeze(actor: Principal, event: Event, *, allow_any_phase: bool = False) -> ResultsSnapshot:
    if event.phase not in FREEZE_PHASES and not allow_any_phase:
        raise conflict(
            "not_deliberation",
            "Freeze results during deliberation, after judging has closed.",
        )
    last = event.results_snapshots.aggregate(n=Max("number"))["n"] or 0
    payload = build_payload(event, last + 1)
    signature = signing.sign(payload)
    snapshot = ResultsSnapshot.objects.create(
        event=event,
        number=last + 1,
        method=payload["method"],
        input_hash=payload["input_hash"],
        payload=payload,
        payload_hash=signing.payload_hash(payload),
        signature=signature.value,
        key_id=signature.key_id,
        public_key=signature.public_key,
        created_by_id=actor.user_id or "",
    )
    audit.record(
        "judging.results_frozen",
        f"Froze results snapshot #{snapshot.number} "
        f"(hash {snapshot.payload_hash[:12]}, {len(payload['decisions'])} decision(s))",
        actor=actor,
        actor_role="organizer",
        event_id=event.pk,
        target=snapshot,
        details={"payload_hash": snapshot.payload_hash, "key_id": snapshot.key_id},
    )
    return snapshot


def latest(event: Event) -> ResultsSnapshot | None:
    return event.results_snapshots.order_by("-number").first()


def is_current(event: Event, snapshot: ResultsSnapshot) -> bool:
    """True while no review score and no decision has changed since the snapshot."""
    same_input = input_hash(compute(event, bootstrap=0)) == snapshot.input_hash
    same_decisions = [d["id"] for d in _decisions(event)] == [
        d["id"] for d in snapshot.payload["decisions"]
    ]
    return same_input and same_decisions


def signed_document(snapshot: ResultsSnapshot) -> dict[str, Any]:
    """What anyone can download and check with the public key."""
    return {
        "payload": snapshot.payload,
        "payload_hash": snapshot.payload_hash,
        "signature": {
            "alg": signing.ALGORITHM,
            "key_id": snapshot.key_id,
            "public_key": snapshot.public_key,
            "signature": snapshot.signature,
        },
    }


def verify(document: dict[str, Any]) -> bool:
    """Intact and signed by this deployment (see ``core.signing.check_document``)."""
    return signing.check_document(document) == ""
