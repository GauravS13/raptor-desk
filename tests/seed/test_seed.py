from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings

from apps.accounts.models import ApiToken, RoleGrant, User
from apps.events.models import Event
from apps.judging.models import Review
from apps.seed import demo, hygiene
from apps.submissions.models import Project, ProjectVersion
from apps.teams.models import Team

FIXTURES = Path(settings.REPO_DIR) / "data" / "fixtures.json"


@pytest.fixture
def seeded(db) -> Event:
    call_command("seed", fixtures=str(FIXTURES), stdout=StringIO())
    return Event.objects.get(pk="evt_01")


def test_fixture_import_counts(seeded: Event) -> None:
    assert seeded.submissions_close_at.isoformat() == "2026-03-01T18:00:00+00:00"
    assert seeded.tracks.count() == 8
    assert seeded.criteria.count() == 3
    assert set(seeded.criteria.values_list("key", flat=True)) == {
        "functionality",
        "quality",
        "innovation",
    }
    assert Team.objects.filter(event=seeded).count() == 40
    assert Project.objects.filter(event=seeded).count() == 40
    assert Review.objects.filter(event=seeded).count() == 126
    assert RoleGrant.objects.filter(event=seeded, role="judge").count() == 30


def test_duplicate_becomes_one_project_with_two_versions(seeded: Event) -> None:
    project = Project.objects.get(pk="prj_07")
    versions = list(project.versions.order_by("n").values_list("source_ref", flat=True))
    assert versions == ["prj_07", "prj_41"]
    assert project.canonical_version.source_ref == "prj_41"
    assert not Project.objects.filter(pk="prj_41").exists()


def test_fixture_ids_are_kept(seeded: Event) -> None:
    assert User.objects.get(pk="jdg_26").email == "jonas.vogel@example.org"
    assert Team.objects.filter(pk="tm_01").exists()
    assert ProjectVersion.objects.get(source_ref="prj_01").name == "Glass Signal"


def test_shared_team_names_are_disambiguated(seeded: Event) -> None:
    names = [t.name for t in Team.objects.filter(event=seeded, name__startswith="StillTrail")]
    assert sorted(names) == ["StillTrail (tm_03)", "StillTrail (tm_30)", "StillTrail (tm_40)"]


def test_gallery_order_follows_the_fixture(seeded: Event) -> None:
    first = Project.objects.filter(event=seeded).order_by("gallery_order")[:3]
    assert [p.canonical_version.name for p in first] == [
        "Glass Signal",
        "Small Meadow",
        "Deep Compass",
    ]


def test_seed_is_idempotent_and_tokens_are_stable(seeded: Event) -> None:
    call_command("seed", fixtures=str(FIXTURES), stdout=StringIO())
    assert Project.objects.filter(event=seeded).count() == 40
    for login in demo.SEED_LOGINS:
        assert ApiToken.authenticate(login.token).email == login.email


def test_checker_identities_have_the_right_roles(seeded: Event) -> None:
    grants = {
        (g.user.email, g.role)
        for g in RoleGrant.objects.filter(event=seeded).select_related("user")
    }
    assert ("organizer@raptor-desk.local", "organizer") in grants
    assert ("jonas.vogel@example.org", "judge") in grants
    assert ("diego.herrera@example.org", "judge") in grants
    assert ("priya1@example.org", "participant") in grants


def test_live_dogfood_event_is_configured_and_open(seeded: Event) -> None:
    event = Event.objects.get(pk=demo.DOGFOOD_EVENT_ID)
    assert event.phase == "submissions"
    assert event.submissions_are_open
    assert event.template_key == "dogfood-2026"


def test_hygiene_report_names_every_trap(seeded: Event) -> None:
    codes = {finding.code for finding in hygiene.findings(seeded)}
    assert {
        "resubmission",
        "flat_liner",
        "single_review_judges",
        "under_reviewed",
        "weak_evidence",
        "missing_feedback",
        "thin_tracks",
    } <= codes


@override_settings(PROFILE="production")
def test_production_profile_creates_no_demo_data(db) -> None:
    call_command("seed", fixtures=str(FIXTURES), stdout=StringIO())
    assert Event.objects.count() == 0
    assert ApiToken.objects.filter(is_seed=True).count() == 0
    with pytest.raises(RuntimeError):
        demo.require_demo_profile()
