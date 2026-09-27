from typing import ClassVar

from django.conf import settings
from django.db import models

from core import clock
from core.ids import new_id


def _team_id() -> str:
    return new_id("tm")


def _member_id() -> str:
    return new_id("tmm")


def _invite_id() -> str:
    return new_id("inv")


class Team(models.Model):
    id = models.CharField(primary_key=True, max_length=40, default=_team_id, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="teams")
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        ordering: ClassVar[list[str]] = ["name"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["event", "name"], name="team_name_unique_per_event")
        ]

    def __str__(self) -> str:
        return self.name


class MemberRole(models.TextChoices):
    LEAD = "lead", "Lead"
    MEMBER = "member", "Member"


class TeamMember(models.Model):
    """One person in one team.

    ``event`` is stored too, so the database enforces one team per person per event.
    """

    id = models.CharField(primary_key=True, max_length=40, default=_member_id, editable=False)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="+")
    role = models.CharField(max_length=10, choices=MemberRole.choices, default=MemberRole.MEMBER)
    joined_at = models.DateTimeField(default=clock.now)

    class Meta:
        ordering: ClassVar[list[str]] = ["joined_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["event", "user"], name="one_team_per_person_per_event")
        ]

    def __str__(self) -> str:
        return f"{self.user} in {self.team}"


class Invite(models.Model):
    """A shareable join link. Only the token's hash is stored."""

    id = models.CharField(primary_key=True, max_length=40, default=_invite_id, editable=False)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="invites")
    token_hash = models.CharField(max_length=64, unique=True)
    created_by_id = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(default=clock.now)
    expires_at = models.DateTimeField()
    uses_left = models.PositiveSmallIntegerField(default=3)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"invite to {self.team}"

    @property
    def is_usable(self) -> bool:
        return self.revoked_at is None and self.uses_left > 0 and self.expires_at > clock.now()
