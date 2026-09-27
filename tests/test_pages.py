import re
from pathlib import Path

import pytest
from django.test import Client

SRC = Path(__file__).resolve().parent.parent / "src"


@pytest.mark.django_db
def test_home_renders_with_strict_security_headers() -> None:
    response = Client().get("/")
    assert response.status_code == 200
    assert b"Raptor Desk" in response.content
    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert response.headers["X-Frame-Options"] == "DENY"


def test_templates_load_no_external_assets() -> None:
    """The portal must work offline, so no template may pull assets from another host."""
    external = re.compile(r"""<(script|link|img)[^>]+(src|href)=["']https?://""", re.I)
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.html")
        if external.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_templates_use_no_inline_styles_or_handlers() -> None:
    """Inline styles and on* handlers would be blocked by the CSP, so they are not allowed."""
    inline = re.compile(r"""\sstyle=["']|\son[a-z]+=["']""", re.I)
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.html")
        if inline.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


@pytest.mark.django_db
def test_the_menu_only_offers_consoles_the_person_can_use(client) -> None:
    from apps.accounts.models import RoleGrant, User
    from apps.events.models import Event

    event = Event.objects.create(slug="menu", name="Menu test")
    participant = User.objects.create_user("p@example.org", "pw-123456789")
    RoleGrant.objects.create(user=participant, event=event, role="participant")
    judge = User.objects.create_user("j@example.org", "pw-123456789")
    RoleGrant.objects.create(user=judge, event=event, role="judge")

    client.force_login(participant)
    page = client.get("/").content.decode()
    assert 'href="/judge"' not in page and 'href="/o/"' not in page
    client.force_login(judge)
    page = client.get("/").content.decode()
    assert 'href="/judge"' in page and 'href="/o/"' not in page
