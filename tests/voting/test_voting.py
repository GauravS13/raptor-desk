from io import StringIO
from pathlib import Path
from unittest import mock

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import Client

from apps.events.models import Event
from apps.seed.demo import EXTENDED_LOGINS, SEED_LOGINS
from apps.voting import services
from apps.voting.models import BallotToken, Vote
from core.models import AuditEvent, OutboxMessage

TOKENS = {login.role: login.token for login in (*SEED_LOGINS, *EXTENDED_LOGINS)}
API = "/api/events/evt_vote"
OWN, OTHER = "prj_v01", "prj_v02"  # participant's own project; another team's


def bearer(role: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {TOKENS[role]}"}


@pytest.fixture
def event(db) -> Event:
    fixtures = Path(settings.REPO_DIR) / "data" / "fixtures.json"
    call_command("seed", fixtures=str(fixtures), stdout=StringIO())
    return Event.objects.get(pk="evt_vote")


@pytest.fixture
def client() -> Client:
    return Client()


def mint(client: Client, n: int) -> list[str]:
    response = client.post(
        f"{API}/ballot-tokens", {"count": n}, content_type="application/json", **bearer("organizer")
    )
    assert response.status_code == 201
    return response.json()["tokens"]


def vote(client: Client, token: str, project: str, **extra):
    return client.post(
        f"/api/ballots/{token}/vote", {"project": project}, content_type="application/json", **extra
    )


def test_results_are_hidden_from_everyone_but_organizers_while_voting(event, client) -> None:
    assert event.voting_is_open
    for path in ("/events/evt_vote/results", "/events/evt_vote/community"):
        response = client.get(path)
        assert response.status_code == 403, path  # never a redirect
    assert client.get(f"{API}/community-results").status_code == 403
    assert client.get(f"{API}/community-results", **bearer("participant")).status_code == 403
    assert client.get(f"{API}/community-results", **bearer("judge_a")).status_code == 403
    tally = client.get(f"{API}/community-results", **bearer("organizer"))
    assert tally.status_code == 200 and len(tally.json()["projects"]) == 8


def test_a_ballot_link_votes_once_and_learns_nothing(event, client) -> None:
    token = mint(client, 1)[0]
    first = vote(client, token, OTHER)
    assert first.status_code == 201
    assert not {"count", "votes", "tally", "rank", "score"} & set(first.json())
    again = vote(client, token, "prj_v03")
    assert again.status_code == 409
    ballot = BallotToken.objects.get(token_hash=services._hash(token))
    audit = AuditEvent.objects.filter(event_id="evt_vote", target_id__in=[ballot.pk])
    assert audit.filter(action="vote.rejected").count() == 1
    assert Vote.objects.filter(token=ballot).count() == 1


def test_forged_ballots_are_refused_audited_and_rate_limited(event, client) -> None:
    assert vote(client, "tk_evt_vote.forged123", OTHER).status_code == 404
    forged = AuditEvent.objects.filter(event_id="evt_vote", action="vote.rejected_forged")
    assert forged.count() == 1 and "@" not in forged.first().summary
    codes = [vote(client, f"tk_evt_vote.forged{i}", OTHER).status_code for i in range(30)]
    assert 429 in codes
    limited = vote(client, "tk_evt_vote.forged-late", OTHER)
    assert limited.status_code == 429 and int(limited["Retry-After"]) > 0


def test_nobody_votes_for_their_own_team(event, client) -> None:
    own = client.post(
        f"{API}/votes", {"project": OWN}, content_type="application/json", **bearer("participant")
    )
    assert own.status_code == 422 and "own team" in own.json()["error"]["message"]
    other = client.post(
        f"{API}/votes", {"project": OWN}, content_type="application/json", **bearer("participant_b")
    )
    assert other.status_code == 201
    ballot = client.get(f"{API}/ballot", **bearer("participant")).json()
    assert OWN not in [p["id"] for p in ballot["projects"]]


def test_one_email_link_per_person(event, client) -> None:
    def ask(email: str):
        return client.post(f"{API}/vote-links", {"email": email}, content_type="application/json")

    assert ask("voter@example.org").status_code == 202
    assert ask("  VOTER@Example.org ").status_code == 409
    assert ask("voter+again@example.org").status_code == 409  # plus-addresses are one person
    mails = [m for m in OutboxMessage.objects.all() if "voting link" in m.payload["subject"]]
    assert len(mails) == 1
    link = next(w for w in mails[0].payload["body"].split() if "/vote/" in w)
    token = link.rsplit("/", 1)[1]
    assert client.get(f"/vote/{token}").status_code == 200
    assert vote(client, token, OTHER).status_code == 201


def test_ballot_order_is_stable_per_voter_and_differs_between_voters(event, client) -> None:
    first, second = mint(client, 2)

    def order(token: str) -> list[str]:
        return [p["id"] for p in client.get(f"/api/ballots/{token}").json()["projects"]]

    assert order(first) == order(first)
    orders = {tuple(order(t)) for t in mint(client, 6)} | {
        tuple(order(first)),
        tuple(order(second)),
    }
    assert len(orders) > 1
    gallery = [client.get("/events/evt_vote/projects").content for _ in range(2)]
    assert gallery[0] == gallery[1]


def test_comments_need_an_account_and_are_escaped(event, client) -> None:
    body = {"body": "<script>alert('x')</script> nice"}
    assert (
        client.post(
            f"/api/projects/{OTHER}/comments", body, content_type="application/json"
        ).status_code
        == 401
    )
    created = client.post(
        f"/api/projects/{OTHER}/comments",
        body,
        content_type="application/json",
        **bearer("participant"),
    )
    assert created.status_code == 201
    page = client.get(f"/projects/{OTHER}").content.decode()
    assert "&lt;script&gt;alert(" in page and "<script>alert(" not in page


def test_votes_in_a_burst_are_held_for_review(event, client) -> None:
    tokens = mint(client, services.BURST_VOTES + 2)
    for token in tokens:
        assert vote(client, token, OTHER).status_code == 201  # voters see no difference
    held = Vote.objects.filter(event=event, status="quarantined")
    assert held.count() == 2
    tally = client.get(f"{API}/community-results", **bearer("organizer")).json()
    row = next(r for r in tally["projects"] if r["project_id"] == OTHER)
    assert (row["votes"], row["held"]) == (services.BURST_VOTES, 2)
    reviewed = client.post(
        f"{API}/votes/review",
        {"vote_ids": [v.pk for v in held], "accept": True},
        content_type="application/json",
        **bearer("organizer"),
    )
    assert reviewed.json() == {"updated": 2}


def test_quadratic_ballots_spend_credits(event, client) -> None:
    event.voting_scheme = "quadratic"
    event.qv_credits = 9
    event.save()
    token = mint(client, 1)[0]
    spend = client.post(
        f"/api/ballots/{token}/vote",
        {"project": OTHER, "credits": 9},
        content_type="application/json",
    )
    assert spend.status_code == 201
    over = client.post(
        f"/api/ballots/{token}/vote",
        {"project": "prj_v03", "credits": 1},
        content_type="application/json",
    )
    assert over.status_code == 409
    tally = client.get(f"{API}/community-results", **bearer("organizer")).json()
    assert next(r for r in tally["projects"] if r["project_id"] == OTHER)["weight"] == 3.0


def test_results_open_to_everyone_after_voting_closes(event, client) -> None:
    from datetime import timedelta

    from core import clock

    later = clock.now() + timedelta(days=61)
    with mock.patch("core.clock.now", return_value=later):
        assert client.get(f"{API}/community-results").status_code == 200
        assert vote(client, mint(client, 1)[0], OTHER).status_code == 409
