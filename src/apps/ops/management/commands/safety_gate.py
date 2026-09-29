from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import ApiToken, User
from apps.seed.demo import ADMIN_EMAIL, DEMO_PASSWORD, EXTENDED_LOGINS, SEED_LOGINS


class Command(BaseCommand):
    help = (
        "Refuse to start in the production profile while demo credentials exist: fixed seed "
        "tokens, or demo accounts that still accept the published demo password."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        if settings.PROFILE != "production":
            self.stdout.write("safety gate: demo profile, demo credentials are expected")
            return
        problems = []
        seeded = ApiToken.objects.filter(is_seed=True, revoked_at__isnull=True).count()
        if seeded:
            problems.append(f"{seeded} active fixed demo token(s)")
        emails = [ADMIN_EMAIL, *(login.email for login in (*SEED_LOGINS, *EXTENDED_LOGINS))]
        weak = [
            user.email
            for user in User.objects.filter(email__in=emails)
            if user.has_usable_password() and user.check_password(DEMO_PASSWORD)
        ]
        if weak:
            problems.append(f"{len(weak)} account(s) with the public demo password")
        if problems:
            raise CommandError(
                "refusing to start in production: "
                + "; ".join(problems)
                + ". This data volume was seeded for the demo; start from an empty volume, "
                "or revoke the tokens and change those passwords."
            )
        self.stdout.write("safety gate: no demo credentials in production")
