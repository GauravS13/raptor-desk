import pytest


@pytest.fixture(autouse=True)
def _fast_password_hashing(settings) -> None:
    """Production keeps Django's slow PBKDF2 hasher; tests use a fast one."""
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
