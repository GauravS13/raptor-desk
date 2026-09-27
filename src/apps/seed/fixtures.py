"""Load the official DOGFOOD fixtures into the portal's own schema.

The file is input, not our data model. The loader keeps every fixture id,
validates references, and resolves the awkward cases deliberately:

* A team that submitted the same project twice (same team, title and repo)
  becomes one project with two versions; the later one is canonical.
* Distinct teams that share a display name keep their ids; names are made
  unique by appending the fixture id.
* Missing scores stay missing. Nothing is imputed.
"""

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.db import transaction

from apps.accounts.models import JudgeProfile, RoleGrant, User
from apps.events.models import Criterion, Event, Phase, Track, Weighting
from apps.judging.models import Assignment, JudgeTrack, Review, ScoreItem
from apps.submissions.models import Project, ProjectStatus, ProjectVersion
from apps.teams.models import Team, TeamMember

REQUIRED_KEYS = ("event", "tracks", "judges", "teams", "projects", "scores")


class FixtureError(ValueError):
    pass


@dataclass
class ImportResult:
    event: Event
    merged_duplicates: list[dict[str, Any]] = field(default_factory=list)
    renamed_teams: list[dict[str, str]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _validate(data: dict[str, Any]) -> None:
    missing = [key for key in REQUIRED_KEYS if key not in data]
    if missing:
        raise FixtureError(f"fixture file is missing: {', '.join(missing)}")
    tracks = {t["id"] for t in data["tracks"]}
    judges = {j["id"] for j in data["judges"]}
    teams = {t["id"] for t in data["teams"]}
    projects = {p["id"] for p in data["projects"]}
    for judge in data["judges"]:
        unknown = set(judge.get("tracks", [])) - tracks
        if unknown:
            raise FixtureError(f"judge {judge['id']} refers to unknown tracks {sorted(unknown)}")
    for project in data["projects"]:
        if project["team"] not in teams or project["track"] not in tracks:
            raise FixtureError(f"project {project['id']} refers to an unknown team or track")
    for score in data["scores"]:
        if score["judge"] not in judges or score["project"] not in projects:
            raise FixtureError(f"a score refers to unknown judge or project: {score}")


def _duplicate_key(project: dict[str, Any]) -> tuple[str, str, str]:
    return (project["team"], project["title"].strip().casefold(), project.get("repo_url", ""))


def _user(email: str, name: str = "", user_id: str | None = None) -> User:
    user = User.objects.filter(email=email.lower()).first()
    if user is not None:
        return user
    extra: dict[str, Any] = {"name": name}
    if user_id:
        extra["id"] = user_id
    return User.objects.create_user(email, **extra)


@transaction.atomic
def load(path: Path, *, slug: str = "sample-hack-2026") -> ImportResult:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    _validate(data)
    raw_event = data["event"]
    event = Event.objects.create(
        id=raw_event["id"],
        slug=slug,
        name=raw_event["name"],
        phase=Phase.JUDGING,
        submissions_close_at=_parse_time(raw_event["submissions_close"]),
        weighting=Weighting.MULTIPLIER,
        template_key="fixture-default",
        description="The official DOGFOOD 2026 fixture dataset, imported as-is.",
    )
    result = ImportResult(event=event)

    # Criteria come from the score keys actually present (three, not the two in the spec sample).
    criteria_keys: list[str] = []
    for score in data["scores"]:
        for key in score["criteria"]:
            if key not in criteria_keys:
                criteria_keys.append(key)
    criteria = {
        key: Criterion.objects.create(
            event=event,
            key=key,
            label=key.replace("_", " ").title(),
            weight=Decimal("1"),
            min_score=1,
            max_score=5,
            order=index,
        )
        for index, key in enumerate(criteria_keys)
    }

    tracks = {
        t["id"]: Track.objects.create(id=t["id"], event=event, name=t["name"], order=index)
        for index, t in enumerate(data["tracks"])
    }

    judges: dict[str, User] = {}
    for raw in data["judges"]:
        user = _user(raw["email"], raw["name"], user_id=raw["id"])
        judges[raw["id"]] = user
        RoleGrant.objects.get_or_create(user=user, event=event, role="judge")
        JudgeProfile.objects.get_or_create(
            user=user, defaults={"skill_tags": [tracks[t].name for t in raw.get("tracks", [])]}
        )
        for track_id in raw.get("tracks", []):
            JudgeTrack.objects.create(event=event, judge=user, track=tracks[track_id])

    name_counts: dict[str, int] = defaultdict(int)
    for raw in data["teams"]:
        name_counts[raw["name"]] += 1
    teams: dict[str, Team] = {}
    for raw in data["teams"]:
        name = raw["name"]
        if name_counts[name] > 1:
            name = f"{raw['name']} ({raw['id']})"
            result.renamed_teams.append({"team": raw["id"], "from": raw["name"], "to": name})
        team = Team.objects.create(id=raw["id"], event=event, name=name)
        teams[raw["id"]] = team
        for index, email in enumerate(raw["members"]):
            member = _user(email)
            TeamMember.objects.create(
                team=team, user=member, event=event, role="lead" if index == 0 else "member"
            )
            RoleGrant.objects.get_or_create(user=member, event=event, role="participant")

    # Group resubmissions, then create one project per group with a version per entry.
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for raw in data["projects"]:
        groups[_duplicate_key(raw)].append(raw)
    version_of: dict[str, ProjectVersion] = {}
    for order, entries in enumerate(groups.values(), start=1):
        entries.sort(key=lambda e: e["submitted_at"])
        first = entries[0]
        project = Project.objects.create(
            id=first["id"],
            event=event,
            team=teams[first["team"]],
            track=tracks[first["track"]],
            status=ProjectStatus.SUBMITTED,
            gallery_order=order,
            created_at=_parse_time(first["submitted_at"]),
        )
        version = None
        for n, raw in enumerate(entries, start=1):
            submitted = _parse_time(raw["submitted_at"])
            version = ProjectVersion.objects.create(
                project=project,
                n=n,
                name=raw["title"],
                tagline=raw.get("summary", ""),
                repo_url=raw.get("repo_url", ""),
                source="fixture",
                source_ref=raw["id"],
                created_at=submitted,
                submitted_at=submitted,
            )
            version_of[raw["id"]] = version
        project.canonical_version = version
        project.save(update_fields=["canonical_version"])
        if len(entries) > 1:
            result.merged_duplicates.append(
                {
                    "project": project.pk,
                    "title": first["title"],
                    "entries": [e["id"] for e in entries],
                    "team": first["team"],
                }
            )

    assignments: dict[tuple[str, str], Assignment] = {}
    for raw in data["scores"]:
        version = version_of[raw["project"]]
        judge = judges[raw["judge"]]
        pair = (judge.pk, version.project_id)
        assignment = assignments.get(pair)
        if assignment is None:
            assignment = Assignment.objects.create(
                event=event,
                judge=judge,
                project_id=version.project_id,
                source="fixture",
                status="done",
                reason="Imported from the fixture file",
            )
            assignments[pair] = assignment
        review = Review.objects.create(
            assignment=assignment,
            event=event,
            judge=judge,
            project_id=version.project_id,
            version=version,
            comment=raw.get("comment", ""),
            status="submitted",
            started_at=version.submitted_at,
            submitted_at=version.submitted_at,
        )
        for key, value in raw["criteria"].items():
            ScoreItem.objects.create(review=review, criterion=criteria[key], value=int(value))

    result.counts = {
        "tracks": len(tracks),
        "judges": len(judges),
        "teams": len(teams),
        "fixture_projects": len(data["projects"]),
        "projects": len(groups),
        "scores": len(data["scores"]),
        "criteria": len(criteria),
    }
    return result
