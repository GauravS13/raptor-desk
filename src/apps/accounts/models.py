import hashlib
import secrets
from typing import Any, ClassVar

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models

from core import clock
from core.ids import new_id


def _user_id() -> str:
    return new_id("usr")


def _token_id() -> str:
    return new_id("tok")


def normalize_email(email: str) -> str:
    """Emails are compared case-insensitively everywhere, so they are stored lowercase."""
    return email.strip().lower()


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email: str, password: str | None = None, **extra: Any) -> "User":
        if not email:
            raise ValueError("An email address is required")
        user = self.model(email=normalize_email(email), **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str | None = None, **extra: Any) -> "User":
        extra.update(is_staff=True, is_superuser=True, is_admin=True)
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """A person. Their roles are granted per event, not stored here (see RoleGrant).

    ``is_admin`` is the only global role: platform administrators.
    """

    id = models.CharField(primary_key=True, max_length=40, default=_user_id, editable=False)
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=200, blank=True)
    affiliation = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False, help_text="Can open the Django admin.")
    is_admin = models.BooleanField(default=False, help_text="Platform administrator.")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    class Meta:
        ordering: ClassVar[list[str]] = ["email"]

    def __str__(self) -> str:
        return self.name or self.email

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.email = normalize_email(self.email)
        super().save(*args, **kwargs)

    @property
    def display_name(self) -> str:
        return self.name or self.email.split("@", 1)[0]


class ApiToken(models.Model):
    """A bearer token for the REST API. Only a SHA-256 hash of the token is stored."""

    id = models.CharField(primary_key=True, max_length=40, default=_token_id, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="api_tokens")
    token_hash = models.CharField(max_length=64, unique=True)
    label = models.CharField(max_length=100)
    is_seed = models.BooleanField(
        default=False, help_text="Fixed demo token for the acceptance checker (demo profile only)."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.label} ({self.user})"

    @staticmethod
    def hash(raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def issue(cls, user: User, label: str) -> tuple["ApiToken", str]:
        """Create a token and return it with the raw value, which is shown exactly once."""
        raw = "rd_" + secrets.token_urlsafe(32)
        token = cls.objects.create(user=user, label=label, token_hash=cls.hash(raw))
        return token, raw

    @classmethod
    def authenticate(cls, raw: str) -> User | None:
        token = (
            cls.objects.select_related("user")
            .filter(token_hash=cls.hash(raw), revoked_at__isnull=True, user__is_active=True)
            .first()
        )
        if token is None:
            return None
        cls.objects.filter(pk=token.pk).update(last_used_at=clock.now())
        return token.user

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
