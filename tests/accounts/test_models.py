import pytest

from apps.accounts.models import ApiToken, User


@pytest.mark.django_db
def test_user_email_is_normalised_and_unique_case_insensitively() -> None:
    user = User.objects.create_user("  Priya1@Example.ORG ", "pw-not-used-12345")
    assert user.email == "priya1@example.org"
    assert user.id.startswith("usr_")
    with pytest.raises(Exception):  # noqa: B017 - IntegrityError, driver specific subclass
        User.objects.create_user("PRIYA1@example.org")


@pytest.mark.django_db
def test_user_without_password_cannot_log_in_with_password() -> None:
    user = User.objects.create_user("judge@example.org")
    assert not user.has_usable_password()


@pytest.mark.django_db
def test_superuser_is_platform_admin() -> None:
    admin = User.objects.create_superuser("root@example.org", "a-long-password-123")
    assert admin.is_admin and admin.is_staff and admin.is_superuser


@pytest.mark.django_db
def test_api_token_stores_only_a_hash_and_authenticates() -> None:
    user = User.objects.create_user("org@example.org")
    token, raw = ApiToken.issue(user, "cli")
    assert raw.startswith("rd_")
    assert token.token_hash == ApiToken.hash(raw)
    assert raw not in token.token_hash
    assert ApiToken.authenticate(raw) == user
    assert ApiToken.authenticate("rd_wrong") is None


@pytest.mark.django_db
def test_revoked_token_and_inactive_user_are_rejected() -> None:
    user = User.objects.create_user("org@example.org")
    token, raw = ApiToken.issue(user, "cli")
    ApiToken.objects.filter(pk=token.pk).update(revoked_at="2026-01-01T00:00:00Z")
    assert ApiToken.authenticate(raw) is None

    _, raw2 = ApiToken.issue(user, "cli2")
    User.objects.filter(pk=user.pk).update(is_active=False)
    assert ApiToken.authenticate(raw2) is None
