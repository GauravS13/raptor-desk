import re

import pytest
from django.core import mail
from django.test import Client

from apps.accounts.models import ApiToken, MagicLink, User
from core import outbox
from core.handlers import send_email  # noqa: F401  (registers the email handler)

PASSWORD = "a-long-password-123"


@pytest.fixture
def user(db) -> User:
    return User.objects.create_user("judge@example.org", PASSWORD)


def test_login_page_renders(db) -> None:
    response = Client().get("/login")
    assert response.status_code == 200
    assert b'name="password"' in response.content


def test_password_login_redirects_to_safe_next(user: User) -> None:
    client = Client()
    response = client.post(
        "/login", {"email": "JUDGE@example.org", "password": PASSWORD, "next": "/healthz"}
    )
    assert response.status_code == 302
    assert response["Location"] == "/healthz"
    assert client.get("/").context["principal"].email == "judge@example.org"


def test_login_refuses_open_redirects(user: User) -> None:
    response = Client().post(
        "/login",
        {"email": "judge@example.org", "password": PASSWORD, "next": "https://evil.example/x"},
    )
    assert response["Location"] == "/"


def test_wrong_password_shows_generic_error(user: User) -> None:
    response = Client().post("/login", {"email": "judge@example.org", "password": "nope"})
    assert response.status_code == 200
    assert b"Email or password is incorrect." in response.content


def test_login_is_rate_limited_per_email(user: User) -> None:
    client = Client()
    for _ in range(8):
        client.post("/login", {"email": "judge@example.org", "password": "nope"})
    response = client.post("/login", {"email": "judge@example.org", "password": PASSWORD})
    assert response.status_code == 429
    assert b"Too many sign-in attempts" in response.content


def test_logout_requires_post(user: User) -> None:
    client = Client()
    client.force_login(user)
    assert client.get("/logout").status_code == 405
    assert client.post("/logout").status_code == 302
    assert not client.get("/").context["principal"].is_authenticated


def _deliver_mail() -> None:
    outbox.process_due()


def test_magic_link_signs_in_once(user: User) -> None:
    client = Client()
    response = client.post("/login/email", {"email": "judge@example.org"})
    assert response.status_code == 200
    _deliver_mail()
    assert len(mail.outbox) == 1
    token = re.search(r"/login/magic/(\S+)", mail.outbox[0].body).group(1)

    # GET only shows a confirmation, so link scanners cannot use the token up.
    assert client.get(f"/login/magic/{token}").status_code == 200
    assert MagicLink.objects.get().used_at is None

    assert client.post(f"/login/magic/{token}").status_code == 302
    assert client.get("/").context["principal"].email == "judge@example.org"
    assert Client().post(f"/login/magic/{token}").status_code == 400


def test_magic_link_does_not_reveal_unknown_addresses(db) -> None:
    response = Client().post("/login/email", {"email": "nobody@example.org"})
    assert response.status_code == 200
    assert b"If that address has an account" in response.content
    _deliver_mail()
    assert mail.outbox == []


def test_api_token_exchange_and_revoke(user: User) -> None:
    client = Client()
    response = client.post(
        "/api/auth/token",
        {"email": "judge@example.org", "password": PASSWORD, "label": "laptop"},
        content_type="application/json",
    )
    assert response.status_code == 200
    body = response.json()
    auth = {"HTTP_AUTHORIZATION": f"Bearer {body['token']}"}
    assert client.get("/api/me", **auth).status_code == 200

    revoked = client.delete(f"/api/auth/tokens/{body['token_id']}", **auth)
    assert revoked.status_code == 200
    assert client.get("/api/me", **auth).status_code == 401


def test_api_token_exchange_rejects_bad_password(user: User) -> None:
    response = Client().post(
        "/api/auth/token",
        {"email": "judge@example.org", "password": "nope"},
        content_type="application/json",
    )
    assert response.status_code == 401
    assert ApiToken.objects.count() == 0


def test_cannot_revoke_someone_elses_token(user: User) -> None:
    other = User.objects.create_user("other@example.org")
    other_token, _ = ApiToken.issue(other, "theirs")
    _, raw = ApiToken.issue(user, "mine")
    response = Client().delete(
        f"/api/auth/tokens/{other_token.pk}", HTTP_AUTHORIZATION=f"Bearer {raw}"
    )
    assert response.status_code == 404
    other_token.refresh_from_db()
    assert other_token.revoked_at is None
