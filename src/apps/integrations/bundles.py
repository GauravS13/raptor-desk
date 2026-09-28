"""Event bundles: a whole event as one JSON document, and back.

An export holds the event's settings, tracks, prizes, rubric, the people
involved, teams, projects with every version, and every review with its
scores. ``manifest.sha256`` is the SHA-256 of the canonical JSON of everything
else in the bundle, so an import refuses a bundle that was changed after it
was exported ("manifest mismatch").

An import creates a new event with new ids. People are matched by email (or
created); imported reviews enter the new event's score ledger. This is how an
organizer moves an event between deployments, keeps an archive, or starts a
new edition from an old one.
"""

from collections import defaultdict
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils.text import slugify

from apps.accounts.models import RoleGrant, User
from apps.events.models import Criterion, Event, Prize, Track
from apps.judging import ledger
from apps.judging.models import Assignment, JudgeTrack, Review, ScoreItem
from apps.submissions.models import Project, ProjectVersion
from apps.teams.models import Team, TeamMember
from core import audit, clock, signing
from core.http import unprocessable
from core.policy import Principal

FORMAT = "raptor-desk/event-bundle"
VERSION = 1
EVENT_FIELDS = (
    "name",
    "description",
    "phase",
    "weighting",
    "reviews_per_project",
    "max_load_per_judge",
    "blind_judging",
    "max_team_size",
    "voting_mode",
    "voting_scheme",
    "qv_credits",
)
TIME_FIELDS = (
    "registration_opens_at",
    "registration_closes_at",
    "submissions_open_at",
    "submissions_close_at",
    "judging_closes_at",
    "voting_opens_at",
    "voting_closes_at",
)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _event_fields(event: Event) -> dict[str, Any]:
    data = {f: getattr(event, f) for f in EVENT_FIELDS if hasattr(event, f)}
    data.update({f: _iso(getattr(event, f)) for f in TIME_FIELDS if hasattr(event, f)})
    data["id"] = event.pk
    return data


def export(event: Event) -> dict[str, Any]:
    people: dict[str, dict[str, str]] = {}

    def person(user: User) -> str:
        people[user.pk] = {"id": user.pk, "email": user.email, "name": user.name}
        return user.pk

    judges: dict[str, list[str]] = defaultdict(list)
    for grant in RoleGrant.objects.filter(event=event, role="judge").select_related("user"):
        judges[person(grant.user)] = []
    for jt in JudgeTrack.objects.filter(event=event).select_related("judge"):
        judges[person(jt.judge)].append(jt.track_id)

    teams = []
    for team in Team.objects.filter(event=event).order_by("pk"):
        members = [
            {"person": person(m.user), "role": m.role}
            for m in TeamMember.objects.filter(team=team).select_related("user").order_by("pk")
        ]
        teams.append({"id": team.pk, "name": team.name, "members": members})

    projects = []
    for project in Project.objects.filter(event=event).order_by("gallery_order", "pk"):
        versions = [
            {
                "n": v.n,
                "name": v.name,
                "tagline": v.tagline,
                "description": v.description,
                "repo_url": v.repo_url,
                "video_url": v.video_url,
                "live_url": v.live_url,
                "tech_tags": v.tech_tags,
                "submitted_at": _iso(v.submitted_at),
            }
            for v in project.versions.order_by("n")
        ]
        canonical = project.canonical_version.n if project.canonical_version else None
        projects.append(
            {
                "id": project.pk,
                "team": project.team_id,
                "track": project.track_id,
                "status": project.status,
                "gallery_order": project.gallery_order,
                "canonical": canonical,
                "versions": versions,
            }
        )

    reviews = []
    for review in (
        Review.objects.filter(event=event)
        .select_related("judge", "version")
        .prefetch_related("scores__criterion")
        .order_by("submitted_at", "pk")
    ):
        reviews.append(
            {
                "judge": person(review.judge),
                "project": review.project_id,
                "version": review.version.n,
                "status": review.status,
                "comment": review.comment,
                "improvement": review.improvement,
                "submitted_at": _iso(review.submitted_at),
                "scores": {s.criterion.key: s.value for s in review.scores.all()},
            }
        )

    body = {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": clock.now().isoformat(),
        "event": _event_fields(event),
        "tracks": [
            {"id": t.pk, "name": t.name, "description": t.description, "order": t.order}
            for t in Track.objects.filter(event=event).order_by("order", "pk")
        ],
        "prizes": [
            {"rank": p.rank, "name": p.name, "amount": p.amount, "track": p.track_id}
            for p in Prize.objects.filter(event=event).order_by("rank", "pk")
        ],
        "criteria": [
            {
                "key": c.key,
                "label": c.label,
                "description": c.description,
                "weight": str(c.weight),
                "min_score": c.min_score,
                "max_score": c.max_score,
                "anchors": c.anchors,
                "is_gate": c.is_gate,
                "gate_threshold": None if c.gate_threshold is None else str(c.gate_threshold),
                "is_bonus": c.is_bonus,
                "order": c.order,
            }
            for c in Criterion.objects.filter(event=event).order_by("order", "key")
        ],
        "people": sorted(people.values(), key=lambda p: p["id"]),
        "judges": [{"person": pid, "tracks": sorted(t)} for pid, t in sorted(judges.items())],
        "teams": teams,
        "projects": projects,
        "reviews": reviews,
    }
    counts = {k: len(body[k]) for k in ("tracks", "criteria", "teams", "projects", "reviews")}
    body["manifest"] = {"sha256": signing.payload_hash(body), "counts": counts}
    return body


def check(bundle: Any) -> dict[str, Any]:
    if not isinstance(bundle, dict) or bundle.get("format") != FORMAT:
        raise unprocessable("This is not a Raptor Desk event bundle.")
    manifest = bundle.get("manifest") or {}
    body = {k: v for k, v in bundle.items() if k != "manifest"}
    if signing.payload_hash(body) != manifest.get("sha256"):
        raise unprocessable(
            "manifest mismatch: the bundle was changed after it was exported.",
            {"code": "manifest_mismatch"},
        )
    return body


def _parse(value: str | None) -> Any:
    from datetime import datetime

    return datetime.fromisoformat(value) if value else None


def _unique_slug(name: str) -> str:
    base = slugify(name)[:60] or "event"
    slug, n = base, 2
    while Event.objects.filter(slug=slug).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


@transaction.atomic
def import_bundle(
    actor: Principal, bundle: Any, name: str, *, event_id: str | None = None
) -> Event:
    body = check(bundle)
    name = (name or body["event"]["name"]).strip()[:200]
    source = body["event"]
    fields = {f: source[f] for f in EVENT_FIELDS if f in source}
    fields.update({f: _parse(source.get(f)) for f in TIME_FIELDS if f in source})
    fields["name"] = name
    event = Event.objects.create(
        **({"id": event_id} if event_id else {}), slug=_unique_slug(name), **fields
    )
    RoleGrant.objects.get_or_create(user_id=actor.user_id, event=event, role="organizer")

    tracks = {
        t["id"]: Track.objects.create(
            event=event, name=t["name"], description=t.get("description", ""), order=t["order"]
        )
        for t in body["tracks"]
    }
    for p in body["prizes"]:
        Prize.objects.create(
            event=event,
            rank=p["rank"],
            name=p["name"],
            amount=p.get("amount", ""),
            track=tracks.get(p.get("track")),
        )
    criteria = {}
    for c in body["criteria"]:
        threshold = c.get("gate_threshold")
        criteria[c["key"]] = Criterion.objects.create(
            event=event,
            key=c["key"],
            label=c["label"],
            description=c.get("description", ""),
            weight=Decimal(c["weight"]),
            min_score=c["min_score"],
            max_score=c["max_score"],
            anchors=c.get("anchors", {}),
            is_gate=c.get("is_gate", False),
            gate_threshold=Decimal(threshold) if threshold is not None else None,
            is_bonus=c.get("is_bonus", False),
            order=c.get("order", 0),
        )

    users: dict[str, User] = {}
    for p in body["people"]:
        user = User.objects.filter(email=p["email"].lower()).first()
        if user is None:
            user = User.objects.create_user(p["email"], None, name=p.get("name", ""))
        users[p["id"]] = user
    for j in body["judges"]:
        judge = users[j["person"]]
        RoleGrant.objects.get_or_create(user=judge, event=event, role="judge")
        for track_id in j["tracks"]:
            if track_id in tracks:
                JudgeTrack.objects.create(event=event, judge=judge, track=tracks[track_id])

    teams = {}
    for t in body["teams"]:
        team = Team.objects.create(event=event, name=t["name"])
        teams[t["id"]] = team
        for m in t["members"]:
            member = users[m["person"]]
            TeamMember.objects.create(team=team, user=member, event=event, role=m["role"])
            RoleGrant.objects.get_or_create(user=member, event=event, role="participant")

    projects: dict[str, Project] = {}
    versions: dict[tuple[str, int], ProjectVersion] = {}
    for p in body["projects"]:
        project = Project.objects.create(
            event=event,
            team=teams[p["team"]],
            track=tracks.get(p.get("track")),
            status=p["status"],
            gallery_order=p.get("gallery_order", 0),
        )
        projects[p["id"]] = project
        for v in p["versions"]:
            version = ProjectVersion.objects.create(
                project=project,
                n=v["n"],
                name=v["name"],
                tagline=v.get("tagline", ""),
                description=v.get("description", ""),
                repo_url=v.get("repo_url", ""),
                video_url=v.get("video_url", ""),
                live_url=v.get("live_url", ""),
                tech_tags=v.get("tech_tags", []),
                source="import",
                submitted_at=_parse(v.get("submitted_at")),
            )
            versions[(p["id"], v["n"])] = version
        if p.get("canonical") is not None:
            project.canonical_version = versions[(p["id"], p["canonical"])]
            project.save(update_fields=["canonical_version"])

    assignments: dict[tuple[str, str], Assignment] = {}
    for r in body["reviews"]:
        judge = users[r["judge"]]
        project = projects[r["project"]]
        pair = (judge.pk, project.pk)
        if pair not in assignments:
            assignments[pair] = Assignment.objects.create(
                event=event,
                judge=judge,
                project=project,
                source="import",
                status="done" if r["status"] == "submitted" else "active",
                reason="Imported from an event bundle",
            )
        review = Review.objects.create(
            assignment=assignments[pair],
            event=event,
            judge=judge,
            project=project,
            version=versions[(r["project"], r["version"])],
            comment=r.get("comment", ""),
            improvement=r.get("improvement", ""),
            status=r["status"],
            submitted_at=_parse(r.get("submitted_at")),
        )
        for key, value in r["scores"].items():
            ScoreItem.objects.create(review=review, criterion=criteria[key], value=int(value))
        if review.status == "submitted":
            ledger.append(review, "imported")

    audit.record(
        "event.imported",
        f"Imported event bundle as '{name}' "
        f"({len(projects)} projects, {len(body['reviews'])} reviews)",
        actor=actor,
        actor_role="organizer",
        event_id=event.pk,
        target=event,
        details={"source_event": source.get("id"), "manifest": bundle["manifest"]["sha256"]},
    )
    return event
