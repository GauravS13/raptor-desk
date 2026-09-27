from django.http import HttpRequest
from ninja import Router, Schema

from apps.accounts import services
from apps.accounts.models import ApiToken
from core import audit, clock
from core.http import not_found
from core.policy import Rule, define, get_principal, policy

router = Router(tags=["auth"])

define("api.auth.token", Rule(public=True, description="Exchange email and password for a token."))
define("api.auth.revoke", Rule(authenticated=True, description="Revoke one of your own tokens."))


class TokenIn(Schema):
    email: str
    password: str
    label: str = "api"


class TokenOut(Schema):
    token_id: str
    token: str
    label: str


class RevokedOut(Schema):
    token_id: str
    revoked: bool


@router.post("/token", response=TokenOut)
@policy("api.auth.token")
def create_token(request: HttpRequest, payload: TokenIn) -> TokenOut:
    """Issue a bearer token. The raw token is returned once and never stored."""
    token, raw = services.issue_token_with_password(
        request, payload.email, payload.password, payload.label
    )
    return TokenOut(token_id=token.pk, token=raw, label=token.label)


@router.delete("/tokens/{token_id}", response=RevokedOut)
@policy("api.auth.revoke")
def revoke_token(request: HttpRequest, token_id: str) -> RevokedOut:
    principal = get_principal(request)
    token = ApiToken.objects.filter(pk=token_id, user_id=principal.user_id).first()
    if token is None:
        raise not_found("No such token.")
    if token.revoked_at is None:
        ApiToken.objects.filter(pk=token.pk).update(revoked_at=clock.now())
        audit.record("auth.token_revoked", f"API token '{token.label}' revoked", target=token)
    return RevokedOut(token_id=token.pk, revoked=True)
