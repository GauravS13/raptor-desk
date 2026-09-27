import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, override_settings

from apps.accounts.models import ApiToken, User
from core.checks import unguarded_routes
from core.policy import ANONYMOUS, Decision, Principal, Rule, decide, define

JUDGE_IN_EVT01 = Principal(user_id="usr_a", auth="bearer", grants={"evt_01": frozenset({"judge"})})
ORGANIZER_EVT02 = Principal(
    user_id="usr_b", auth="bearer", grants={"evt_02": frozenset({"organizer"})}
)
ADMIN = Principal(user_id="usr_c", auth="bearer", is_admin=True)


def test_public_rule_allows_anonymous() -> None:
    assert decide(Rule(public=True), ANONYMOUS) is Decision.ALLOW


def test_non_public_rule_needs_authentication() -> None:
    assert decide(Rule(authenticated=True), ANONYMOUS) is Decision.UNAUTHENTICATED
    assert decide(Rule(authenticated=True), JUDGE_IN_EVT01) is Decision.ALLOW


def test_event_scoped_role_must_be_held_in_that_event() -> None:
    rule = Rule(roles=frozenset({"judge"}))
    assert decide(rule, JUDGE_IN_EVT01, "evt_01") is Decision.ALLOW
    assert decide(rule, JUDGE_IN_EVT01, "evt_02") is Decision.FORBIDDEN
    assert decide(rule, JUDGE_IN_EVT01, None) is Decision.FORBIDDEN


def test_organizer_of_one_event_cannot_act_on_another() -> None:
    rule = Rule(roles=frozenset({"organizer", "admin"}))
    assert decide(rule, ORGANIZER_EVT02, "evt_02") is Decision.ALLOW
    assert decide(rule, ORGANIZER_EVT02, "evt_01") is Decision.FORBIDDEN


def test_admin_passes_rules_that_name_admin_only() -> None:
    assert decide(Rule(roles=frozenset({"organizer", "admin"})), ADMIN, "evt_01") is Decision.ALLOW
    assert decide(Rule(roles=frozenset({"judge"})), ADMIN, "evt_01") is Decision.FORBIDDEN


def test_unscoped_rule_accepts_role_in_any_event() -> None:
    rule = Rule(roles=frozenset({"judge"}), event_scoped=False)
    assert decide(rule, JUDGE_IN_EVT01) is Decision.ALLOW
    assert decide(rule, ORGANIZER_EVT02) is Decision.FORBIDDEN


def test_rules_reject_unknown_roles_and_conflicting_definitions() -> None:
    with pytest.raises(ImproperlyConfigured):
        Rule(roles=frozenset({"superhero"}))
    with pytest.raises(ImproperlyConfigured):
        Rule(public=True, roles=frozenset({"judge"}))
    define("test.same", Rule(public=True))
    define("test.same", Rule(public=True))
    with pytest.raises(ImproperlyConfigured):
        define("test.same", Rule(authenticated=True))


def test_every_route_in_the_project_has_a_policy() -> None:
    assert unguarded_routes() == []


@override_settings(ROOT_URLCONF="tests.core.urls_unguarded")
def test_boot_check_reports_a_route_without_policy() -> None:
    problems = unguarded_routes()
    assert any("forgotten" in problem for problem in problems)


@pytest.mark.django_db
def test_api_accepts_bearer_token() -> None:
    user = User.objects.create_user("org@example.org")
    _, raw = ApiToken.issue(user, "test")
    response = Client().get("/api/me", HTTP_AUTHORIZATION=f"Bearer {raw}")
    assert response.status_code == 200
    assert response.json()["email"] == "org@example.org"
    assert response.json()["auth"] == "bearer"


@pytest.mark.django_db
def test_api_rejects_missing_or_bad_token_with_json_401_and_no_redirect() -> None:
    for headers in ({}, {"HTTP_AUTHORIZATION": "Bearer rd_not_a_token"}):
        response = Client().get("/api/me", **headers)
        assert response.status_code == 401
        assert "Location" not in response.headers
        assert response.json()["error"]["code"] == "unauthorized"


@pytest.mark.django_db
def test_api_ignores_browser_session_cookie() -> None:
    """Ninja views are CSRF-exempt, so the API must not honour session cookies."""
    User.objects.create_user("judge@example.org", "a-long-password-123")
    client = Client()
    assert client.login(email="judge@example.org", password="a-long-password-123")
    assert client.get("/api/me").status_code == 401
