"""Demo data: accounts, the acceptance checker's fixed tokens, and a live DOGFOOD event.

Only created in the ``demo`` profile (the default for ``docker compose up``).
The ``production`` profile refuses to create any of it, so fixed tokens and
known passwords can never exist in a real deployment.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction

from apps.accounts.models import ApiToken, RoleGrant, User
from apps.events import services as event_services
from apps.events.models import Event, Phase
from apps.events.templates_catalog import apply_template
from core import clock
from core.policy import Principal

DEMO_PASSWORD = "raptor-demo-2026"  # noqa: S105  (demo profile only, printed at boot)
DOGFOOD_EVENT_ID = "evt_dogfood_2026"


@dataclass(frozen=True)
class SeedLogin:
    role: str
    email: str
    token: str
    note: str


# The acceptance checker's identities. judge_a and judge_b are peers: both cover
# the Developer tools track and both reviewed prj_40, so isolation is tested
# between two judges who genuinely share work.
SEED_LOGINS = (
    SeedLogin(
        "organizer",
        "organizer@raptor-desk.local",
        "rd_seed_org_7f2a",
        "organizer of every demo event",
    ),
    SeedLogin("judge_a", "jonas.vogel@example.org", "rd_seed_jdg26_91bc", "fixture judge jdg_26"),
    SeedLogin("judge_b", "diego.herrera@example.org", "rd_seed_jdg24_44de", "fixture judge jdg_24"),
    SeedLogin(
        "participant", "priya1@example.org", "rd_seed_prt_priya1_2e88", "member of team tm_01"
    ),
)
ADMIN_EMAIL = "admin@raptor-desk.local"


@dataclass(frozen=True)
class DemoSignIn:
    label: str
    email: str
    note: str
    lands_on: str


def demo_sign_ins() -> list[DemoSignIn]:
    """Accounts offered on the sign-in page and the tour. Empty outside the demo profile."""
    if settings.PROFILE != "demo":
        return []
    organizer, judge_a, judge_b, participant = SEED_LOGINS
    return [
        DemoSignIn("Organizer", organizer.email, "runs both demo events", "/o/"),
        DemoSignIn("Judge A", judge_a.email, "fixture judge jdg_26", "/judge"),
        DemoSignIn("Judge B", judge_b.email, "fixture judge jdg_24, a peer of Judge A", "/judge"),
        DemoSignIn(
            "Participant",
            participant.email,
            "team tm_01; can form a team in DOGFOOD 2026",
            f"/events/{DOGFOOD_EVENT_ID}",
        ),
        DemoSignIn("Admin", ADMIN_EMAIL, "platform administrator", "/o/"),
    ]


def require_demo_profile() -> None:
    if settings.PROFILE != "demo":
        raise RuntimeError("Demo data can only be created in the demo profile.")


def _ensure_token(user: User, raw: str, label: str) -> None:
    ApiToken.objects.update_or_create(
        token_hash=ApiToken.hash(raw),
        defaults={"user": user, "label": label, "is_seed": True, "revoked_at": None},
    )


@transaction.atomic
def ensure_demo_accounts(fixture_event: Event) -> list[SeedLogin]:
    require_demo_profile()
    admin = User.objects.filter(email=ADMIN_EMAIL).first() or User.objects.create_superuser(
        ADMIN_EMAIL, DEMO_PASSWORD, name="Demo admin"
    )
    organizer = User.objects.filter(email=SEED_LOGINS[0].email).first() or User.objects.create_user(
        SEED_LOGINS[0].email, DEMO_PASSWORD, name="Demo organizer"
    )
    RoleGrant.objects.get_or_create(user=organizer, event=fixture_event, role="organizer")
    for login in SEED_LOGINS:
        user = User.objects.get(email=login.email)
        if not user.has_usable_password():
            user.set_password(DEMO_PASSWORD)
            user.save(update_fields=["password"])
        _ensure_token(user, login.token, f"seed:{login.role}")
    _ensure_token(admin, "rd_seed_admin_5c1d", "seed:admin")
    return list(SEED_LOGINS)


@transaction.atomic
def ensure_dogfood_event() -> Event:
    """A live event configured exactly like DOGFOOD 2026, open for submissions."""
    require_demo_profile()
    existing = Event.objects.filter(pk=DOGFOOD_EVENT_ID).first()
    if existing is not None:
        return existing
    organizer = User.objects.get(email=SEED_LOGINS[0].email)
    principal = Principal(user_id=organizer.pk, email=organizer.email)
    now = clock.now()
    event = Event.objects.create(
        id=DOGFOOD_EVENT_ID,
        slug="dogfood-2026",
        name="DOGFOOD 2026 (live demo)",
        description=(
            "Build the platform that will judge you. Configured from the dogfood-2026 "
            "template: a T1 gate, four weighted criteria on a 0 to 5 scale, and bonuses "
            "that only break ties."
        ),
        submissions_open_at=now - timedelta(days=1),
        submissions_close_at=now + timedelta(days=3),
    )
    RoleGrant.objects.get_or_create(user=organizer, event=event, role="organizer")
    apply_template(principal, event, "dogfood-2026")
    event_services.transition(principal, event, Phase.SUBMISSIONS, reason="Demo event opens")
    return event


# --- Community vote demo (the extended checker's T3 event) -------------------------

VOTE_EVENT_ID = "evt_vote"
# participant_b for the extended checker: a member of another team than participant.
EXTENDED_LOGINS = (
    SeedLogin(
        "participant_b", "lena2@example.org", "rd_seed_prt_lena2_61d0", "member of team tm_02"
    ),
)
VOTE_PROJECTS = (
    ("Lantern Queue", "A calmer queue for hackathon demos"),
    ("Tidewatch", "Flood alerts from open river data"),
    ("Mosaic Ledger", "Shared expenses for student societies"),
    ("Quiet Relay", "Low-bandwidth chat for field teams"),
    ("Harbor Lights", "Accessible wayfinding for ferry terminals"),
    ("Paper Compass", "Printable maps that update from a QR code"),
    ("Signal Garden", "A sensor kit for school gardens"),
    ("Night Ferry", "Timetables that work offline"),
)


@transaction.atomic
def ensure_extended_logins() -> list[SeedLogin]:
    require_demo_profile()
    for login in EXTENDED_LOGINS:
        user = User.objects.filter(email=login.email).first()
        if user is not None:
            _ensure_token(user, login.token, f"seed:{login.role}")
    return list(EXTENDED_LOGINS)


@transaction.atomic
def ensure_vote_event() -> Event:
    """A small event whose community vote is open, with fixed project ids.

    participant (priya1) is on team tm_v01 with project prj_v01; participant_b
    (lena2) is on tm_v02 with prj_v02. Six more teams have no members.
    """
    from apps.submissions.models import Project, ProjectStatus, ProjectVersion
    from apps.teams.models import Team, TeamMember

    require_demo_profile()
    existing = Event.objects.filter(pk=VOTE_EVENT_ID).first()
    if existing is not None:
        return existing
    now = clock.now()
    event = Event.objects.create(
        id=VOTE_EVENT_ID,
        slug="community-vote-demo",
        name="Community vote (live demo)",
        description=(
            "A small event with its community vote open: vote by link, by email or with your "
            "account. Results stay hidden until voting closes."
        ),
        phase=Phase.JUDGING,
        submissions_open_at=now - timedelta(days=10),
        submissions_close_at=now - timedelta(days=1),
        voting_mode="all",
        voting_scheme="single",
        voting_opens_at=now - timedelta(hours=1),
        voting_closes_at=now + timedelta(days=60),
    )
    organizer = User.objects.get(email=SEED_LOGINS[0].email)
    RoleGrant.objects.get_or_create(user=organizer, event=event, role="organizer")
    members = {1: SEED_LOGINS[3].email, 2: EXTENDED_LOGINS[0].email}
    for index, (name, tagline) in enumerate(VOTE_PROJECTS, start=1):
        team = Team.objects.create(id=f"tm_v{index:02d}", event=event, name=f"{name} team")
        email = members.get(index)
        if email and (user := User.objects.filter(email=email).first()):
            TeamMember.objects.create(team=team, user=user, event=event, role="lead")
            RoleGrant.objects.get_or_create(user=user, event=event, role="participant")
        project = Project.objects.create(
            id=f"prj_v{index:02d}",
            event=event,
            team=team,
            status=ProjectStatus.SUBMITTED,
            gallery_order=index,
        )
        version = ProjectVersion.objects.create(
            project=project,
            n=1,
            name=name,
            tagline=tagline,
            source="demo",
            submitted_at=now - timedelta(days=2),
        )
        project.canonical_version = version
        project.save(update_fields=["canonical_version"])
    return event


# --- A published archive event, made through the real export, import and publish paths -----

ARCHIVE_EVENT_ID = "evt_archive"
ARCHIVE_REASON = "Demo archive: the panel kept the computed order."


def ensure_archive_event(source: Event) -> Event:
    """Sample Hack 2026 exported, imported as an archive, deliberated and published.

    Everything goes through the ordinary services (bundle import, decisions,
    signed snapshot, publication), so the archive has judge protocols,
    certificates and participation records. Judge A's passport is made public,
    as the demo account's consent. The notification emails this would send to
    the fixture addresses are recorded but not sent.
    """
    from apps.accounts.models import JudgeProfile
    from apps.integrations import bundles
    from apps.judging import deliberation, snapshots
    from core.models import OutboxMessage

    require_demo_profile()
    existing = Event.objects.filter(pk=ARCHIVE_EVENT_ID).first()
    if existing is not None:
        return existing
    organizer = User.objects.get(email=SEED_LOGINS[0].email)
    actor = Principal(user_id=organizer.pk, email=organizer.email)
    before = set(OutboxMessage.objects.values_list("pk", flat=True))
    with transaction.atomic():
        event = bundles.import_bundle(
            actor,
            bundles.export(source),
            "Sample Hack 2025 (archive)",
            event_id=ARCHIVE_EVENT_ID,
            keep_phase=True,
        )
        event_services.transition(actor, event, Phase.DELIBERATION, reason="Judging closed")
        for row in deliberation.board(event).undecided:
            deliberation.record_decision(
                actor, event, kind="confirm", project_id=row.project_id, rationale=ARCHIVE_REASON
            )
        snapshots.freeze(actor, event)
        event_services.transition(actor, event, Phase.PUBLISHED, reason="Demo archive")
        judge_a = User.objects.get(email=SEED_LOGINS[1].email)
        JudgeProfile.objects.update_or_create(user=judge_a, defaults={"public_passport": True})
        OutboxMessage.objects.exclude(pk__in=before).update(
            done_at=clock.now(), last_error="demo archive: not sent"
        )
    return event
