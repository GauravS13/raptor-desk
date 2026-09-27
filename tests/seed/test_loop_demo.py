import re
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.events.models import Event
from apps.judging.models import Review
from apps.seed import loop_demo
from core.models import AuditEvent


@pytest.fixture
def event(db) -> Event:
    fixtures = Path(settings.REPO_DIR) / "data" / "fixtures.json"
    call_command("seed", fixtures=str(fixtures), stdout=StringIO())
    return Event.objects.get(pk="evt_01")


def test_asked_reviews_move_the_close_calls_away_from_a_coin_flip(event: Event) -> None:
    moves = loop_demo.run(event, budget=6)
    assert moves
    for move in moves:
        assert abs(move.after - 0.5) > abs(move.before - 0.5), move


def test_synthetic_reviews_are_labelled_everywhere(event: Event) -> None:
    loop_demo.run(event, budget=4)
    synthetic = Review.objects.filter(assignment__source="close_call")
    assert synthetic.count() == 4
    assert all(r.comment.startswith("[demo]") for r in synthetic)
    assert AuditEvent.objects.filter(action="demo.synthetic_review").count() == 4


def test_the_command_refuses_outside_the_demo_profile(event: Event, settings) -> None:
    settings.PROFILE = "production"
    with pytest.raises(CommandError, match="demo profile"):
        call_command("demo_loop", stdout=StringIO())


def test_the_table_in_judging_md_is_what_the_demo_produces(event: Event) -> None:
    doc = (Path(settings.REPO_DIR) / "JUDGING.md").read_text(encoding="utf-8")
    heading = "| Project | Cutoff | Before | After 6 asked reviews |"
    table = doc.split(heading)[1].split("\n\n")[0]
    documented = {
        m[1]: (int(m[2]), int(m[3]), int(m[4]))
        for m in re.finditer(r"\| (prj_\d+) \| top (\d) \| (\d+)% \| (\d+)%", table)
    }
    produced = {
        m.project: (m.cutoff, round(m.before * 100), round(m.after * 100))
        for m in loop_demo.run(event, budget=6)
    }
    assert produced == documented
