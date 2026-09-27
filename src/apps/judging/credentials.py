"""Signed paperwork: judge protocols, judging certificates, participation records.

- When judging closes (the event enters deliberation), every judge with a
  submitted review gets a **protocol**: each project they reviewed, the
  version, their scores, a hash of their written feedback, and the ledger
  entry that recorded it. Only that judge and the organizers can read it.
- When results are published, every judge gets a **certificate** and every
  member of a team with a submitted project gets a **participation record**.
  Neither contains a score. Both are public to whoever holds their code.

Everything is canonical JSON signed with the deployment key, so a document
can be checked offline against /.well-known/raptor-desk-key.
"""

import secrets
from collections import defaultdict
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Max

from apps.accounts.models import User
from apps.events import hooks
from apps.events.models import Event, Phase
from apps.judging.models import Credential, CredentialKind, Review, ScoreEvent
from apps.judging.scoring import latest_reviews, score_map
from apps.submissions.models import Project
from apps.teams.models import TeamMember
from core import audit, clock, outbox, signing
from core.http import not_found
from core.policy import Principal

KINDS = {
    CredentialKind.PROTOCOL: "raptor-desk/judge-protocol",
    CredentialKind.CERTIFICATE: "raptor-desk/judging-certificate",
    CredentialKind.PARTICIPATION: "raptor-desk/participation-record",
}
PUBLIC_KINDS = (CredentialKind.CERTIFICATE, CredentialKind.PARTICIPATION)


def _new_code() -> str:
    while True:
        code = secrets.token_urlsafe(9)
        if not Credential.objects.filter(code=code).exists():
            return code


def _issue(kind: str, event: Event, user: User, body: dict[str, Any]) -> Credential | None:
    """Create one credential, unless this person already has this kind for this event."""
    if Credential.objects.filter(kind=kind, event=event, user=user).exists():
        return None
    number = (Credential.objects.filter(kind=kind).aggregate(n=Max("number"))["n"] or 0) + 1
    code = _new_code()
    prefix = {"protocol": "P", "certificate": "C", "participation": "R"}[kind]
    payload = {
        "kind": KINDS[kind],
        "number": f"{prefix}-{number:06d}",
        "code": code,
        "issued_at": clock.now().isoformat(),
        "issuer": settings.BASE_URL,
        "event": {"id": event.pk, "name": event.name},
        "person": {"id": user.pk, "name": user.name or user.email.split("@")[0]},
        **body,
    }
    signature = signing.sign(payload)
    return Credential.objects.create(
        kind=kind,
        number=number,
        code=code,
        event=event,
        user=user,
        payload=payload,
        payload_hash=signing.payload_hash(payload),
        signature=signature.value,
        key_id=signature.key_id,
        public_key=signature.public_key,
    )


def signed_document(item: Credential) -> dict[str, Any]:
    return {
        "payload": item.payload,
        "payload_hash": item.payload_hash,
        "signature": {
            "alg": signing.ALGORITHM,
            "key_id": item.key_id,
            "public_key": item.public_key,
            "signature": item.signature,
        },
    }


# --- Issuing --------------------------------------------------------------------------


@transaction.atomic
def issue_protocols(event: Event) -> list[Credential]:
    reviews = latest_reviews(list(Review.objects.filter(event=event).prefetch_related("scores")))
    entries = {
        e.review_id: e for e in ScoreEvent.objects.filter(event=event).order_by("seq")
    }  # later entries overwrite earlier ones: the latest entry per review
    by_judge: dict[str, list[Review]] = defaultdict(list)
    for review in reviews:
        by_judge[review.judge_id].append(review)
    issued = []
    for judge in User.objects.filter(pk__in=by_judge).order_by("pk"):
        rows = []
        for review in sorted(by_judge[judge.pk], key=lambda r: r.project_id):
            entry = entries.get(review.pk)
            rows.append(
                {
                    "project": review.project_id,
                    "version": review.version_id,
                    "scores": dict(sorted(score_map(review).items())),
                    "submitted_at": review.submitted_at.isoformat()
                    if review.submitted_at
                    else None,
                    "ledger_seq": entry.seq if entry else None,
                    "ledger_entry_hash": entry.entry_hash if entry else None,
                }
            )
        credential = _issue(
            CredentialKind.PROTOCOL, event, judge, {"reviews": rows, "count": len(rows)}
        )
        if credential is not None:
            issued.append(credential)
            _mail(judge, event, credential, "Your signed judging protocol")
    return issued


@transaction.atomic
def issue_public_records(event: Event) -> list[Credential]:
    issued = []
    protocols = Credential.objects.filter(event=event, kind=CredentialKind.PROTOCOL)
    for protocol in protocols.select_related("user"):
        credential = _issue(
            CredentialKind.CERTIFICATE,
            event,
            protocol.user,
            {"role": "judge", "projects_reviewed": protocol.payload["count"]},
        )
        if credential is not None:
            issued.append(credential)
            _mail(protocol.user, event, credential, "Your judging certificate")
    projects = {
        p.team_id: p
        for p in Project.objects.filter(event=event, status="submitted").select_related(
            "canonical_version", "team"
        )
    }
    for member in TeamMember.objects.filter(event=event).select_related("user").order_by("pk"):
        project = projects.get(member.team_id)
        if project is None:
            continue
        name = project.canonical_version.name if project.canonical_version else project.pk
        credential = _issue(
            CredentialKind.PARTICIPATION,
            event,
            member.user,
            {
                "role": "participant",
                "team": project.team.name,
                "project": {"id": project.pk, "name": name},
            },
        )
        if credential is not None:
            issued.append(credential)
    return issued


def _mail(user: User, event: Event, credential: Credential, subject: str) -> None:
    link = (
        f"{settings.BASE_URL}/judge/protocols/{credential.code}"
        if credential.kind == CredentialKind.PROTOCOL
        else f"{settings.BASE_URL}/verify/{credential.code}"
    )
    outbox.enqueue(
        "email",
        {
            "to": [user.email],
            "subject": f"{event.name}: {subject.lower()}",
            "body": f"{subject} ({credential.label}) for {event.name} is ready:\n\n{link}\n",
        },
        dedupe_key=f"credential:{credential.pk}",
    )


@hooks.on_enter(Phase.DELIBERATION)
def _on_judging_closed(actor: Principal, event: Event) -> None:
    issued = issue_protocols(event)
    audit.record(
        "judging.protocols_issued",
        f"Issued {len(issued)} signed judge protocol(s)",
        actor=actor,
        actor_role="organizer",
        event_id=event.pk,
    )


@hooks.on_enter(Phase.PUBLISHED)
def _on_published(actor: Principal, event: Event) -> None:
    if not Credential.objects.filter(event=event, kind=CredentialKind.PROTOCOL).exists():
        issue_protocols(event)  # the event skipped deliberation, or predates protocols
    issued = issue_public_records(event)
    audit.record(
        "judging.records_issued",
        f"Issued {len(issued)} certificate(s) and participation record(s)",
        actor=actor,
        actor_role="organizer",
        event_id=event.pk,
    )


# --- Reading ----------------------------------------------------------------------------


def protocol_for(principal: Principal, code: str) -> Credential:
    item = (
        Credential.objects.select_related("event", "user")
        .filter(code=code, kind=CredentialKind.PROTOCOL)
        .first()
    )
    allowed = item is not None and (
        item.user_id == principal.user_id
        or principal.is_admin
        or principal.has_role("organizer", item.event_id)
    )
    if not allowed:
        raise not_found("No such protocol.")
    return item


def public_record(code: str) -> Credential:
    item = (
        Credential.objects.select_related("event", "user")
        .filter(code=code, kind__in=PUBLIC_KINDS)
        .first()
    )
    if item is None:
        raise not_found("No record with that code.")
    return item


def verify(document: dict[str, Any]) -> bool:
    """Intact and signed by this deployment (see ``core.signing.check_document``)."""
    return signing.check_document(document) == ""
