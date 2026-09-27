import re

import pytest

pytestmark = pytest.mark.django_db


def test_api_docs_work_offline_under_the_csp(client) -> None:
    response = client.get("/api/docs")
    assert response.status_code == 200
    html = response.content.decode()
    assert "cdn." not in html and "https://" not in html
    for tag in re.findall(r"<script\b[^>]*>", html):
        assert 'src="/static/' in tag or 'type="application/json"' in tag, tag
    for asset in re.findall(r'(?:src|href)="(/static/ninja/[^"]+)"', html):
        assert asset.endswith((".js", ".css", ".png", ".svg"))


def test_openapi_schema_is_served(client) -> None:
    schema = client.get("/api/openapi.json").json()
    assert schema["info"]["title"]
    assert "/api/events/{event_id}/submissions" in schema["paths"]
