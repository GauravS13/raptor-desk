"""The fixes for the threat model's weaknesses 3, 5 and 6."""

import socket
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import Client

from apps.events.models import Event
from apps.integrations import webhooks
from apps.seed.demo import SEED_LOGINS
from apps.voting import services
from apps.voting.models import Vote
from core.http import ApiError
from core.policy import Principal

TOKENS = {login.role: login.token for login in SEED_LOGINS}


def bearer(role: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {TOKENS[role]}"}


@pytest.fixture
def event(db) -> Event:
    fixtures = Path(settings.REPO_DIR) / "data" / "fixtures.json"
    call_command("seed", fixtures=str(fixtures), stdout=StringIO())
    return Event.objects.get(pk="evt_vote")


def _mint(client: Client, n: int) -> list[str]:
    return client.post(
        "/api/events/evt_vote/ballot-tokens",
        {"count": n},
        content_type="application/json",
        **bearer("organizer"),
    ).json()["tokens"]


def _vote_all(client: Client, tokens: list[str]) -> None:
    for token in tokens:
        client.post(
            f"/api/ballots/{token}/vote", {"project": "prj_v02"}, content_type="application/json"
        )


# --- 6: webhooks cannot reach internal addresses --------------------------------------------


def _admin() -> Principal:
    from apps.accounts.models import User
    from apps.seed.demo import ADMIN_EMAIL

    admin = User.objects.get(email=ADMIN_EMAIL)
    return Principal(user_id=admin.pk, email=admin.email, is_admin=True)


def _resolves_to(address: str):
    return mock.patch.object(
        socket, "getaddrinfo", return_value=[(socket.AF_INET, 0, 0, "", (address, 443))]
    )


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.1.2.3", "192.168.0.10", "169.254.169.254", "0.0.0.0"],  # noqa: S104
)
def test_production_refuses_internal_webhook_targets(event, settings, address) -> None:
    settings.WEBHOOK_ALLOW_PRIVATE = False
    with _resolves_to(address), pytest.raises(ApiError) as error:
        webhooks.create(_admin(), "https://hooks.example.org/x", ["comment.created"])
    assert error.value.status == 422 and "private or internal" in error.value.message


def test_a_public_target_is_accepted_and_rechecked_on_delivery(event, settings) -> None:
    settings.WEBHOOK_ALLOW_PRIVATE = False
    with _resolves_to("93.184.216.34"):
        hook, _ = webhooks.create(_admin(), "https://hooks.example.org/x", ["ping"])
    with _resolves_to("10.0.0.5"), pytest.raises(RuntimeError, match="refused"):
        webhooks.deliver({"webhook": hook.pk, "body": {"id": "d", "event": "ping"}})
    hook.refresh_from_db()
    assert hook.last_status == "blocked"


# --- 5: a crowded venue is not treated as a burst ------------------------------------------------


def test_the_burst_threshold_is_per_event(event) -> None:
    event.vote_burst_limit = 3
    event.save()
    client = Client()
    _vote_all(client, _mint(client, 5))
    assert Vote.objects.filter(event=event, status="quarantined").count() == 2


def test_votes_from_the_trusted_venue_network_are_never_held(event) -> None:
    event.vote_burst_limit = 3
    event.vote_trusted_networks = ["127.0.0.0/8"]  # the test client's address
    event.save()
    client = Client()
    _vote_all(client, _mint(client, 5))
    assert not Vote.objects.filter(event=event, status="quarantined").exists()


# --- 3: email voting can be limited to known domains ------------------------------------


def test_email_links_only_for_allowed_domains(event) -> None:
    event.vote_email_domains = ["uni.example.edu"]
    event.save()
    client = Client()
    refused = client.post(
        "/api/events/evt_vote/vote-links",
        {"email": "someone@gmail.example"},
        content_type="application/json",
    )
    assert refused.status_code == 422 and "uni.example.edu" in refused.json()["error"]["message"]
    allowed = client.post(
        "/api/events/evt_vote/vote-links",
        {"email": "Student@Uni.Example.edu"},
        content_type="application/json",
    )
    assert allowed.status_code == 202


def test_settings_are_validated_and_normalised(event) -> None:
    client = Client()
    ok = client.patch(
        "/api/events/evt_vote",
        {"vote_trusted_networks": ["10.0.0.7/16"], "vote_email_domains": ["@Uni.Example.edu"]},
        content_type="application/json",
        **bearer("organizer"),
    )
    assert ok.status_code == 200
    event.refresh_from_db()
    assert event.vote_trusted_networks == ["10.0.0.0/16"]
    assert event.vote_email_domains == ["uni.example.edu"]
    bad = client.patch(
        "/api/events/evt_vote",
        {"vote_trusted_networks": ["not-a-network"]},
        content_type="application/json",
        **bearer("organizer"),
    )
    assert bad.status_code == 422
    assert services.channel_enabled(event, "email")
