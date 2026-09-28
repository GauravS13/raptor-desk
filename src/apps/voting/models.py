from typing import ClassVar

from django.conf import settings
from django.db import models

from core import clock
from core.ids import new_id


def _token_id() -> str:
    return new_id("tok")


def _vote_id() -> str:
    return new_id("vot")


def _comment_id() -> str:
    return new_id("cmt")


class Channel(models.TextChoices):
    LINK = "link", "Link minted by an organizer"
    EMAIL = "email", "Link sent to a verified email"
    MEMBER = "member", "Signed-in account"


class BallotToken(models.Model):
    """One voter's ballot. Only a SHA-256 of the token is stored, never the token.

    ``seed`` fixes this voter's ballot order: stable for them, different from
    everyone else's. ``email_key`` is a keyed hash of the normalised email, so
    a second request for the same address is recognised without keeping it.
    """

    id = models.CharField(primary_key=True, max_length=40, default=_token_id, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="ballots")
    channel = models.CharField(max_length=10, choices=Channel.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    email_key = models.CharField(max_length=64, blank=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ballots",
    )
    seed = models.PositiveBigIntegerField()
    credits = models.PositiveSmallIntegerField(default=1)
    created_by_id = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["event", "user"],
                condition=models.Q(channel="member"),
                name="one_member_ballot_per_event",
            ),
            models.UniqueConstraint(
                fields=["event", "email_key"],
                condition=~models.Q(email_key=""),
                name="one_email_ballot_per_event",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.id} ({self.channel})"


class VoteStatus(models.TextChoices):
    COUNTED = "counted", "Counted"
    QUARANTINED = "quarantined", "Held for review"
    REJECTED = "rejected", "Rejected after review"


class Vote(models.Model):
    """A vote from one ballot for one project. Voters never see tallies.

    Votes that arrive in a burst from one network are held for an organizer
    to review instead of being counted straight away.
    """

    id = models.CharField(primary_key=True, max_length=40, default=_vote_id, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="votes")
    token = models.ForeignKey(BallotToken, on_delete=models.CASCADE, related_name="votes")
    project = models.ForeignKey(
        "submissions.Project", on_delete=models.CASCADE, related_name="votes"
    )
    credits = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=12, choices=VoteStatus.choices, default=VoteStatus.COUNTED)
    ip_hash = models.CharField(max_length=32, blank=True, db_index=True)
    created_at = models.DateTimeField(default=clock.now, db_index=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["token", "project"], name="one_vote_per_ballot_project")
        ]

    def __str__(self) -> str:
        return f"{self.token_id} -> {self.project_id}"


class Comment(models.Model):
    """A public comment on a project by a signed-in person. Always shown escaped."""

    id = models.CharField(primary_key=True, max_length=40, default=_comment_id, editable=False)
    project = models.ForeignKey(
        "submissions.Project", on_delete=models.CASCADE, related_name="comments"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="comments"
    )
    body = models.TextField(max_length=2000)
    created_at = models.DateTimeField(default=clock.now)
    hidden_at = models.DateTimeField(null=True, blank=True)
    hidden_by_id = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.author_id} on {self.project_id}"
