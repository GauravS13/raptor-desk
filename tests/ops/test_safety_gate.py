from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import ApiToken


@pytest.fixture
def seeded(db) -> None:
    fixtures = Path(settings.REPO_DIR) / "data" / "fixtures.json"
    call_command("seed", fixtures=str(fixtures), stdout=StringIO())


def test_demo_profile_passes(seeded) -> None:
    out = StringIO()
    call_command("safety_gate", stdout=out)
    assert "demo profile" in out.getvalue()


def test_production_refuses_a_demo_seeded_volume(seeded, settings) -> None:
    settings.PROFILE = "production"
    with pytest.raises(CommandError, match="refusing to start in production"):
        call_command("safety_gate", stdout=StringIO())


def test_production_starts_once_demo_credentials_are_gone(seeded, settings) -> None:
    from apps.accounts.models import User
    from apps.seed.demo import DEMO_PASSWORD

    settings.PROFILE = "production"
    ApiToken.objects.filter(is_seed=True).delete()
    for user in User.objects.all():
        if user.has_usable_password() and user.check_password(DEMO_PASSWORD):
            user.set_password("a-long-private-password-123")
            user.save()
    out = StringIO()
    call_command("safety_gate", stdout=out)
    assert "no demo credentials" in out.getvalue()


@pytest.mark.django_db
def test_an_empty_production_volume_starts(settings) -> None:
    settings.PROFILE = "production"
    call_command("safety_gate", stdout=StringIO())
