"""Publishing results: preflight checks, and what happens when an event enters "published".

Publishing is a phase change (Overview tab, or the lifecycle API), so the
organizer sees these checks next to the others and can override a blocking
one only with a written reason, which is kept in the phase history.
"""

from apps.events import hooks, preflight
from apps.events.models import Event, Phase
from apps.judging import deliberation, snapshots
from apps.judging.models import Publication, Review
from core import audit
from core.policy import Principal

BLOCKING, WARNING, Check = preflight.BLOCKING, preflight.WARNING, preflight.Check


@preflight.register(Phase.PUBLISHED)
def results_are_frozen_and_decided(event: Event) -> list[Check]:
    checks = []
    latest = snapshots.latest(event)
    if latest is None:
        checks.append(
            Check(BLOCKING, "no_snapshot", "Freeze the results first (Deliberation tab).")
        )
    elif not snapshots.is_current(event, latest):
        checks.append(
            Check(
                BLOCKING,
                "snapshot_out_of_date",
                f"Reviews or decisions changed after snapshot #{latest.number}; freeze again.",
            )
        )
    undecided = deliberation.board(event).undecided
    if undecided:
        listed = ", ".join(row.project_id for row in undecided)
        checks.append(
            Check(
                BLOCKING,
                "undecided_close_calls",
                f"{len(undecided)} close call(s) on the prize places have no decision: {listed}.",
            )
        )
    return checks


@preflight.register(Phase.PUBLISHED)
def teams_get_written_feedback(event: Event) -> list[Check]:
    silent = (
        Review.objects.filter(event=event, status="submitted", comment="")
        .values("project_id")
        .distinct()
        .count()
    )
    if silent:
        return [
            Check(
                WARNING,
                "missing_feedback",
                f"{silent} project(s) have at least one review without a written comment.",
            )
        ]
    return []


@hooks.on_enter(Phase.PUBLISHED)
def record_publication(actor: Principal, event: Event) -> None:
    snapshot = snapshots.latest(event)
    if snapshot is None:
        # Only reachable by overriding the "no_snapshot" check: freeze now, so
        # what is published is still a signed, immutable document.
        snapshot = snapshots.freeze(actor, event, allow_any_phase=True)
    Publication.objects.create(event=event, snapshot=snapshot, published_by_id=actor.user_id or "")
    audit.record(
        "judging.results_published",
        f"Published results snapshot #{snapshot.number} (SHA-256 {snapshot.payload_hash[:12]})",
        actor=actor,
        actor_role="organizer",
        event_id=event.pk,
        target=snapshot,
    )
    from apps.judging import feedback  # feedback imports this module

    feedback.notify_teams(event)


def published(event: Event) -> Publication | None:
    return Publication.objects.select_related("snapshot").filter(event=event).first()
