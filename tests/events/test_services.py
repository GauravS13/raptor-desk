from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.accounts.models import RoleGrant, User
from apps.events import services
from apps.events.models import Event, Phase, PhaseTransition
from apps.events.services import CriterionSpec
from apps.events.templates_catalog import apply_template, catalog
from core import clock
from core.http import ApiError
from core.models import AuditEvent
from core.policy import Principal

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


@pytest.fixture
def organizer(db) -> Principal:
    user = User.objects.create_user("org@example.org")
    return Principal(user_id=user.pk, email=user.email)


@pytest.fixture
def event(organizer: Principal) -> Event:
    return services.create_event(
        organizer, "Spring Hack", submissions_close_at=NOW + timedelta(days=2)
    )


def specs(*weights: int) -> list[CriterionSpec]:
    return [
        CriterionSpec(key=f"c{i}", label=f"C{i}", weight=Decimal(w)) for i, w in enumerate(weights)
    ]


def test_creator_becomes_organizer_and_it_is_audited(event: Event, organizer: Principal) -> None:
    assert event.slug == "spring-hack"
    assert RoleGrant.objects.filter(
        user_id=organizer.user_id, event=event, role="organizer"
    ).exists()
    assert AuditEvent.objects.filter(action="event.created", event_id=event.pk).exists()


def test_slugs_are_unique(organizer: Principal, event: Event) -> None:
    second = services.create_event(organizer, "Spring Hack")
    assert second.slug == "spring-hack-2"


def test_update_rejects_inverted_windows(event: Event, organizer: Principal) -> None:
    with pytest.raises(ApiError) as excinfo:
        services.update_event(organizer, event, submissions_open_at=NOW + timedelta(days=5))
    assert excinfo.value.status == 422


def test_percent_rubric_must_sum_to_100(event: Event, organizer: Principal) -> None:
    with pytest.raises(ApiError) as excinfo:
        services.set_rubric(organizer, event, "percent", specs(40, 25, 20))
    assert "sum to 100" in str(excinfo.value.details["problems"])
    created = services.set_rubric(organizer, event, "percent", specs(40, 25, 20, 15))
    assert [c.key for c in created] == ["c0", "c1", "c2", "c3"]


def test_bonus_and_gate_do_not_count_toward_percent() -> None:
    rubric = [
        *specs(60, 40),
        CriterionSpec(
            key="gate",
            label="Gate",
            weight=Decimal(0),
            min_score=0,
            max_score=1,
            is_gate=True,
            gate_threshold=Decimal(1),
        ),
        CriterionSpec(
            key="bonus", label="Bonus", weight=Decimal(5), min_score=0, max_score=1, is_bonus=True
        ),
    ]
    assert services.spec_problems("percent", rubric) == []


def test_rubric_locks_once_judging_starts(event: Event, organizer: Principal) -> None:
    Event.objects.filter(pk=event.pk).update(phase=Phase.JUDGING)
    event.refresh_from_db()
    with pytest.raises(ApiError) as excinfo:
        services.set_rubric(organizer, event, "percent", specs(100))
    assert excinfo.value.code == "rubric_locked"


def test_every_template_is_valid_and_dogfood_matches_the_published_rules() -> None:
    templates = catalog()
    expected = {
        "dogfood-2026",
        "zero-dependency-2026",
        "mindcode-2026",
        "code-resurrection-2025",
        "fixture-default",
    }
    assert expected <= set(templates)
    dogfood = templates["dogfood-2026"]
    scored = {c.key: c.weight for c in dogfood.criteria if not c.is_bonus and not c.is_gate}
    assert scored == {
        "tier_completion": 40,
        "judging_integrity": 25,
        "adoptability": 20,
        "code_quality": 15,
    }
    assert [c.key for c in dogfood.criteria if c.is_gate] == ["t1_cleared"]
    bonus = {c.key: c.weight for c in dogfood.criteria if c.is_bonus}
    assert bonus == {
        "normalization_proof": 5,
        "pairwise_mode": 5,
        "threat_model": 3,
        "api_first": 3,
    }


def test_apply_template_configures_the_event(event: Event, organizer: Principal) -> None:
    apply_template(organizer, event, "dogfood-2026")
    event.refresh_from_db()
    assert event.template_key == "dogfood-2026"
    assert event.criteria.count() == 9
    assert event.prizes.count() == 7
    assert services.rubric_problems(event) == []


def test_multiplier_template_is_valid(event: Event, organizer: Principal) -> None:
    apply_template(organizer, event, "code-resurrection-2025")
    event.refresh_from_db()
    assert event.weighting == "multiplier"
    assert event.criteria.get(key="technical_implementation").weight == Decimal("1.4")


def test_transitions_only_move_forward(event: Event, organizer: Principal) -> None:
    with pytest.raises(ApiError) as excinfo:
        services.transition(organizer, event, Phase.JUDGING)
    assert excinfo.value.code == "invalid_transition"


def test_preflight_blocks_until_overridden_with_a_reason(
    event: Event, organizer: Principal
) -> None:
    with clock.frozen(NOW):
        with pytest.raises(ApiError) as excinfo:
            services.transition(organizer, event, Phase.SUBMISSIONS)
        assert excinfo.value.code == "preflight_blocked"
        codes = {c["code"] for c in excinfo.value.details["checks"]}
        assert "rubric" in codes

        with pytest.raises(ApiError):
            services.transition(organizer, event, Phase.SUBMISSIONS, override=True)

        entry = services.transition(
            organizer, event, Phase.SUBMISSIONS, reason="Rubric comes tomorrow", override=True
        )
    assert entry.overridden
    event.refresh_from_db()
    assert event.phase == Phase.SUBMISSIONS
    assert AuditEvent.objects.filter(action="event.phase_changed", event_id=event.pk).exists()


def test_clean_transition_needs_no_override(event: Event, organizer: Principal) -> None:
    services.set_rubric(organizer, event, "percent", specs(100))
    with clock.frozen(NOW):
        entry = services.transition(organizer, event, Phase.SUBMISSIONS)
    assert not entry.overridden
    assert PhaseTransition.objects.filter(event=event).count() == 1


def test_judging_needs_closed_submissions_and_judges(event: Event, organizer: Principal) -> None:
    services.set_rubric(organizer, event, "percent", specs(100))
    Event.objects.filter(pk=event.pk).update(phase=Phase.ELIGIBILITY)
    event.refresh_from_db()
    with clock.frozen(NOW), pytest.raises(ApiError) as excinfo:
        services.transition(organizer, event, Phase.JUDGING)
    codes = {c["code"] for c in excinfo.value.details["checks"]}
    assert {"submissions_open", "no_judges"} <= codes


def test_grants_and_last_organizer_protection(event: Event, organizer: Principal) -> None:
    grant = services.grant_role(organizer, event, "Judge@Example.org", "judge", name="A Judge")
    assert grant.user.email == "judge@example.org"
    assert services.grant_role(organizer, event, "judge@example.org", "judge").pk == grant.pk
    services.revoke_role(organizer, event, grant.user_id, "judge")
    with pytest.raises(ApiError) as excinfo:
        services.revoke_role(organizer, event, organizer.user_id, "organizer")
    assert excinfo.value.code == "last_organizer"
