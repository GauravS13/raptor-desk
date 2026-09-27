from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.events.models import Event
from apps.judging import ledger
from core import signing


@pytest.fixture
def event(db, settings, tmp_path) -> Event:
    settings.DATA_DIR = tmp_path
    signing.signing_key.cache_clear()
    fixtures = Path(settings.REPO_DIR) / "data" / "fixtures.json"
    call_command("seed", fixtures=str(fixtures), stdout=StringIO())
    yield Event.objects.get(pk="evt_01")
    signing.signing_key.cache_clear()


def test_the_tamper_is_found_and_then_undone(event: Event) -> None:
    out = StringIO()
    call_command("demo_tamper", stdout=out)
    text = out.getvalue()
    assert "before: valid, 126 entries" in text
    assert "after:  INVALID at entry " in text and "changed outside the portal" in text
    assert "restored: valid" in text
    assert ledger.verify(event).valid


def test_keep_leaves_the_ledger_invalid(event: Event) -> None:
    call_command("demo_tamper", "--keep", stdout=StringIO())
    assert not ledger.verify(event).valid


def test_refused_outside_the_demo_profile(event: Event, settings) -> None:
    settings.PROFILE = "production"
    with pytest.raises(CommandError, match="demo profile"):
        call_command("demo_tamper", stdout=StringIO())
