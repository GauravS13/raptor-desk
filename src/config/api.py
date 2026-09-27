"""The REST API root. Every operation is guarded by @policy; auth is the bearer token."""

from django.http import HttpRequest, HttpResponse
from ninja import NinjaAPI, Schema

from core.http import ApiError, from_api_error
from core.policy import Rule, define, get_principal, policy, register_api

api = register_api(
    NinjaAPI(
        title="Raptor Desk API",
        version="1",
        description=(
            "Every action available in the Raptor Desk UI, as a documented REST API. "
            "Authenticate with `Authorization: Bearer <token>`."
        ),
        urls_namespace="api",
    )
)


@api.exception_handler(ApiError)
def handle_api_error(request: HttpRequest, exc: ApiError) -> HttpResponse:
    return from_api_error(exc)


define("api.me", Rule(authenticated=True, description="Any signed-in user can see who they are."))


class MeOut(Schema):
    user_id: str
    email: str
    is_admin: bool
    auth: str
    roles: dict[str, list[str]]


@api.get("/me", response=MeOut, tags=["account"])
@policy("api.me")
def me(request: HttpRequest) -> MeOut:
    principal = get_principal(request)
    return MeOut(
        user_id=principal.user_id or "",
        email=principal.email or "",
        is_admin=principal.is_admin,
        auth=principal.auth,
        roles={event: sorted(roles) for event, roles in principal.grants.items()},
    )
