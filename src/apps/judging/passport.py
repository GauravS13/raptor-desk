"""The Judge Passport: a judge's verified judging record across events.

Opt-in and off by default. When a judge switches it on, /judges/{id}/passport
lists their judging certificates: which events, how many projects, when, and
a verify link for each. It never shows scores, and it never lists events the
judge has not been certified for yet.
"""

from django.db import transaction

from apps.accounts.models import JudgeProfile, User
from apps.judging.models import Credential, CredentialKind
from core import audit
from core.http import not_found
from core.policy import Principal


def is_public(user_id: str) -> bool:
    return JudgeProfile.objects.filter(user_id=user_id, public_passport=True).exists()


@transaction.atomic
def set_public(principal: Principal, public: bool) -> bool:
    profile, _ = JudgeProfile.objects.get_or_create(user_id=principal.user_id)
    profile.public_passport = public
    profile.save(update_fields=["public_passport"])
    audit.record(
        "judging.passport_changed",
        "Judge passport made public" if public else "Judge passport made private",
        actor=principal,
        actor_role="judge",
        target=profile,
    )
    return public


def passport(user_id: str) -> tuple[User, list[Credential]]:
    user = User.objects.filter(pk=user_id).first()
    if user is None or not is_public(user_id):
        raise not_found("This judge has no public passport.")
    items = (
        Credential.objects.filter(user=user, kind=CredentialKind.CERTIFICATE)
        .select_related("event")
        .order_by("-issued_at")
    )
    return user, list(items)
