import re
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command

from apps.seed.demo import DEMO_PASSWORD, demo_sign_ins

FIXTURES = Path(settings.REPO_DIR) / "data" / "fixtures.json"


@pytest.fixture
def seeded(db) -> None:
    call_command("seed", fixtures=str(FIXTURES), stdout=StringIO())


@pytest.mark.django_db
def test_sign_in_page_offers_demo_accounts_only_in_demo(client, settings) -> None:
    page = client.get("/login").content.decode()
    assert page.count('class="demo-login"') == len(demo_sign_ins()) == 5
    settings.PROFILE = "production"
    page = client.get("/login").content.decode()
    assert "demo-login" not in page and DEMO_PASSWORD not in page


def test_a_demo_button_is_an_ordinary_sign_in(client, seeded) -> None:
    organizer = demo_sign_ins()[0]
    response = client.post(
        "/login", {"email": organizer.email, "password": DEMO_PASSWORD, "next": "/o/"}
    )
    assert response.status_code == 302 and response["Location"] == "/o/"
    assert client.get("/o/").status_code == 200


def test_demo_buttons_keep_the_page_the_visitor_asked_for(client) -> None:
    page = client.get("/login?next=/judge").content.decode()
    assert page.count('name="next" value="/judge"') == 1 + 5


def test_every_tour_link_leads_somewhere(client, seeded) -> None:
    page = client.get("/tour")
    assert page.status_code == 200
    links = {
        link
        for link in re.findall(r'href="(/[^"]*)"', page.content.decode())
        if not link.startswith("/static/")
    }
    assert len(links) > 10
    for link in sorted(links):
        status = client.get(link).status_code
        # Pages redirect to sign-in; /metrics is for programs, so it answers 401.
        expected = (401,) if link == "/metrics" else (200, 302)
        assert status in expected, f"{link} -> {status}"


@pytest.mark.django_db
def test_no_tour_in_production(client, settings) -> None:
    settings.PROFILE = "production"
    assert client.get("/tour").status_code == 404
