import pytest
from django.test import Client

from apps.accounts.models import User


@pytest.fixture
def admin_client(db) -> Client:
    client = Client()
    client.force_login(User.objects.create_user("admin@example.org", is_admin=True))
    return client


def test_unknown_object_in_a_page_is_a_404_page_not_a_server_error(admin_client: Client) -> None:
    response = admin_client.get("/o/events/evt_does_not_exist")
    assert response.status_code == 404
    assert b"No such event." in response.content


def test_unknown_object_in_the_api_is_json_404(admin_client: Client) -> None:
    from apps.accounts.models import ApiToken

    _, raw = ApiToken.issue(User.objects.get(email="admin@example.org"), "t")
    response = Client().get(
        "/api/events/evt_does_not_exist/people", HTTP_AUTHORIZATION=f"Bearer {raw}"
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
