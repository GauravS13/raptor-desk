"""CSV exports at every stage of an event. The first line is always the header.

Cells that a spreadsheet would treat as a formula (starting with = + - @ or a
tab/CR) are prefixed with an apostrophe, so an exported file cannot run
anything when an organizer opens it.
"""

import csv
import io
from collections.abc import Iterable, Iterator

from apps.accounts.models import RoleGrant
from apps.events.models import Event
from apps.judging import repositories
from apps.judging import results as results_service
from apps.judging.models import Assignment
from apps.judging.scoring import engine_criteria, latest_reviews, review_scores, score_map
from apps.submissions.models import Project
from apps.teams.models import Team
from core.models import AuditEvent

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe(value: object) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(FORMULA_PREFIXES) else text


def to_csv(header: list[str], rows: Iterable[Iterable[object]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([safe(cell) for cell in row])
    return buffer.getvalue()


def results(event: Event) -> str:
    """Ranked results: raw, shrunk z and additive scores, movement, 90% interval, P(top-k)."""
    computed = results_service.compute(event)
    cutoffs = computed.evaluation.cutoffs
    header = [
        "rank",
        "project_id",
        "project",
        "team",
        "reviews",
        "informative_reviews",
        "raw_mean",
        "raw_rank",
        "shrunk_z",
        "additive",
        "movement",
        "ci90_low",
        "ci90_high",
        *[f"p_top{c}" for c in cutoffs],
        "bonus_tiebreak",
        "flags",
        "method",
    ]
    rows = [
        (
            row["rank"],
            row["project_id"],
            row["project"],
            row["team"],
            row["reviews"],
            row["informative_reviews"],
            f"{row['raw']:.4f}",
            row["raw_rank"],
            f"{row['shrunk_z']:.4f}",
            f"{row['additive']:.4f}",
            row["movement"],
            f"{row['ci_low']:.4f}",
            f"{row['ci_high']:.4f}",
            *[f"{row['p_top'][c]:.3f}" for c in cutoffs],
            f"{row['bonus']:.2f}",
            ";".join(row["flags"]),
            computed.method,
        )
        for row in results_service.as_rows(computed)
    ]
    gated = [
        (
            "",
            pid,
            computed.names.get(pid, pid),
            computed.teams.get(pid, ""),
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            *["" for _ in cutoffs],
            "",
            "gate_not_passed",
            computed.method,
        )
        for pid in computed.gated_out
    ]
    return to_csv(header, [*rows, *gated])


def reviews_csv(event: Event) -> str:
    criteria = [c.key for c in engine_criteria(event)]
    reviews = list(repositories.reviews_for_organizer(event.pk))
    latest = {r.pk for r in latest_reviews(reviews)}
    composites = {s.review_id: s.composite for s in review_scores(event, reviews)}
    rows = []
    for review in reviews:
        scores = score_map(review)
        composite = composites.get(review.pk)
        rows.append(
            (
                review.pk,
                review.project_id,
                review.version.n,
                review.judge_id,
                review.status,
                "yes" if review.pk in latest else "no",
                *[scores.get(key, "") for key in criteria],
                f"{composite:.4f}" if composite is not None else "",
                review.comment,
                review.submitted_at.isoformat() if review.submitted_at else "",
            )
        )
    header = [
        "review_id",
        "project_id",
        "version",
        "judge_id",
        "status",
        "counts",
        *criteria,
        "composite",
        "comment",
        "submitted_at",
    ]
    return to_csv(header, rows)


def assignments_csv(event: Event) -> str:
    rows = (
        (a.pk, a.judge_id, a.project_id, a.source, a.status, a.reason, a.created_at.isoformat())
        for a in Assignment.objects.filter(event=event).order_by("judge_id", "project_id")
    )
    header = ["assignment_id", "judge_id", "project_id", "source", "status", "reason", "created_at"]
    return to_csv(header, rows)


def teams_csv(event: Event) -> str:
    def rows() -> Iterator[tuple[object, ...]]:
        for team in Team.objects.filter(event=event).prefetch_related("members__user"):
            for member in team.members.all():
                yield (
                    team.pk,
                    team.name,
                    member.user.email,
                    member.role,
                    member.joined_at.isoformat(),
                )

    return to_csv(["team_id", "team", "email", "role", "joined_at"], rows())


def submissions_csv(event: Event) -> str:
    def rows() -> Iterator[tuple[object, ...]]:
        projects = Project.objects.filter(event=event).select_related("team", "track")
        for project in projects.prefetch_related("versions"):
            for version in project.versions.all():
                yield (
                    project.pk,
                    project.team.name,
                    project.track.name if project.track else "",
                    project.status,
                    version.n,
                    "yes" if project.canonical_version_id == version.pk else "no",
                    version.name,
                    version.tagline,
                    version.repo_url,
                    version.video_url,
                    version.submitted_at.isoformat() if version.submitted_at else "",
                    version.source_ref,
                )

    header = [
        "project_id",
        "team",
        "track",
        "status",
        "version",
        "canonical",
        "name",
        "tagline",
        "repo_url",
        "video_url",
        "submitted_at",
        "source_ref",
    ]
    return to_csv(header, rows())


def registrations_csv(event: Event) -> str:
    grants = RoleGrant.objects.filter(event=event).select_related("user").order_by("role")
    rows = ((g.user.email, g.user.name, g.role, g.created_at.isoformat()) for g in grants)
    return to_csv(["email", "name", "role", "granted_at"], rows)


def audit_csv(event: Event) -> str:
    entries = AuditEvent.objects.filter(event_id=event.pk).order_by("at")
    rows = (
        (
            e.at.isoformat(),
            e.actor_id,
            e.actor_role,
            e.action,
            e.target_type,
            e.target_id,
            e.summary,
        )
        for e in entries
    )
    header = ["at", "actor_id", "actor_role", "action", "target_type", "target_id", "summary"]
    return to_csv(header, rows)


EXPORTS = {
    "results": results,
    "reviews": reviews_csv,
    "assignments": assignments_csv,
    "teams": teams_csv,
    "submissions": submissions_csv,
    "registrations": registrations_csv,
    "audit": audit_csv,
}
