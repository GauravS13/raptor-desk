from typing import ClassVar

from django.conf import settings
from django.db import models

from core import clock
from core.ids import new_id


def _judge_track_id() -> str:
    return new_id("jtr")


def _assignment_id() -> str:
    return new_id("asg")


def _review_id() -> str:
    return new_id("rev")


def _score_id() -> str:
    return new_id("sci")


def _conflict_id() -> str:
    return new_id("coi")


def _decision_id() -> str:
    return new_id("dec")


def _snapshot_id() -> str:
    return new_id("snp")


class JudgeTrack(models.Model):
    """Which tracks a judge covers in an event. A judge never sees another track's projects."""

    id = models.CharField(primary_key=True, max_length=40, default=_judge_track_id, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="judge_tracks")
    judge = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="judge_tracks"
    )
    track = models.ForeignKey("events.Track", on_delete=models.CASCADE, related_name="judges")

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["event", "judge", "track"], name="judge_track_unique")
        ]

    def __str__(self) -> str:
        return f"{self.judge} covers {self.track}"


class AssignmentSource(models.TextChoices):
    BATCH = "batch", "Assigned by an organizer"
    AUTO = "auto", "Assigned by the algorithm"
    CLOSE_CALL = "close_call", "Extra review for a close call"
    FIXTURE = "fixture", "Imported"


class AssignmentStatus(models.TextChoices):
    PROPOSED = "proposed", "Proposed"
    ACTIVE = "active", "Active"
    DONE = "done", "Done"
    WITHDRAWN = "withdrawn", "Withdrawn"


class Assignment(models.Model):
    """A judge is asked to review a project. One per judge and project."""

    id = models.CharField(primary_key=True, max_length=40, default=_assignment_id, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="assignments")
    judge = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assignments"
    )
    project = models.ForeignKey(
        "submissions.Project", on_delete=models.CASCADE, related_name="assignments"
    )
    source = models.CharField(max_length=12, choices=AssignmentSource.choices)
    status = models.CharField(
        max_length=10, choices=AssignmentStatus.choices, default=AssignmentStatus.ACTIVE
    )
    reason = models.CharField(max_length=300, blank=True)
    queue_position = models.PositiveIntegerField(
        default=0, help_text="Randomised per judge so review order does not bias scores."
    )
    created_by_id = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        ordering: ClassVar[list[str]] = ["judge_id", "queue_position"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["judge", "project"], name="one_assignment_per_pair")
        ]

    def __str__(self) -> str:
        return f"{self.judge} → {self.project}"


class ReviewStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"


class Review(models.Model):
    """A judge's review of one version of a project.

    A judge can review a later version too (a resubmission), so reviews are
    unique per assignment and version. The scoring engine uses each judge's
    review of the most recent version they saw.
    """

    id = models.CharField(primary_key=True, max_length=40, default=_review_id, editable=False)
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="reviews")
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="reviews")
    judge = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews"
    )
    project = models.ForeignKey(
        "submissions.Project", on_delete=models.CASCADE, related_name="reviews"
    )
    version = models.ForeignKey(
        "submissions.ProjectVersion", on_delete=models.PROTECT, related_name="reviews"
    )
    comment = models.TextField(blank=True)
    improvement = models.TextField(blank=True, help_text="One thing the team should do next.")
    status = models.CharField(
        max_length=10, choices=ReviewStatus.choices, default=ReviewStatus.DRAFT
    )
    started_at = models.DateTimeField(default=clock.now)
    submitted_at = models.DateTimeField(null=True, blank=True)
    active_seconds = models.PositiveIntegerField(default=0)

    class Meta:
        ordering: ClassVar[list[str]] = ["-submitted_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["assignment", "version"], name="one_review_per_version")
        ]

    def __str__(self) -> str:
        return f"review by {self.judge_id} of {self.project_id} v{self.version_id}"


class ScoreItem(models.Model):
    """One criterion's score within a review. Range is validated against the criterion."""

    id = models.CharField(primary_key=True, max_length=40, default=_score_id, editable=False)
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="scores")
    criterion = models.ForeignKey(
        "events.Criterion", on_delete=models.PROTECT, related_name="scores"
    )
    value = models.SmallIntegerField()

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["review", "criterion"], name="one_score_per_criterion")
        ]

    def __str__(self) -> str:
        return f"{self.criterion_id}={self.value}"


class Conflict(models.Model):
    """A declared conflict of interest: the judge must not review this team's project."""

    id = models.CharField(primary_key=True, max_length=40, default=_conflict_id, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="conflicts")
    judge = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conflicts"
    )
    team = models.ForeignKey("teams.Team", on_delete=models.CASCADE, related_name="conflicts")
    reason = models.CharField(max_length=300, blank=True)
    declared_by_id = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["event", "judge", "team"], name="conflict_unique")
        ]

    def __str__(self) -> str:
        return f"{self.judge} conflicts with {self.team}"


class DecisionKind(models.TextChoices):
    CONFIRM = "confirm", "Confirm the computed place"
    PLACE_ABOVE = "place_above", "Place directly above another project"


class RankingDecision(models.Model):
    """A deliberation decision about the final order, with its reason. Append-only.

    Decisions are applied in the order they were made. To change one, record a
    new decision; the history of what was decided and why is never rewritten.
    """

    id = models.CharField(primary_key=True, max_length=40, default=_decision_id, editable=False)
    event = models.ForeignKey(
        "events.Event", on_delete=models.CASCADE, related_name="ranking_decisions"
    )
    kind = models.CharField(max_length=12, choices=DecisionKind.choices)
    project = models.ForeignKey("submissions.Project", on_delete=models.CASCADE, related_name="+")
    other = models.ForeignKey(
        "submissions.Project", on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    rationale = models.TextField()
    actor_id = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        ordering: ClassVar[list[str]] = ["created_at", "id"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=(
                    models.Q(kind="confirm", other__isnull=True)
                    | models.Q(kind="place_above", other__isnull=False)
                ),
                name="decision_other_matches_kind",
            ),
            models.CheckConstraint(
                condition=~models.Q(rationale=""), name="decision_needs_rationale"
            ),
        ]

    def __str__(self) -> str:
        if self.kind == DecisionKind.PLACE_ABOVE:
            return f"{self.project_id} above {self.other_id}"
        return f"{self.project_id} confirmed"


class ResultsSnapshot(models.Model):
    """Results frozen at a moment, signed by the deployment key. Append-only.

    ``payload`` is the exact document that was signed: the final ranking with
    its uncertainty, the deliberation decisions applied, the method and its
    parameters, and ``input_hash`` over every review score that went in. Anyone
    holding the public key can check that a published result was not altered.
    """

    id = models.CharField(primary_key=True, max_length=40, default=_snapshot_id, editable=False)
    event = models.ForeignKey(
        "events.Event", on_delete=models.CASCADE, related_name="results_snapshots"
    )
    number = models.PositiveIntegerField()
    method = models.CharField(max_length=20)
    input_hash = models.CharField(max_length=64)
    payload = models.JSONField()
    payload_hash = models.CharField(max_length=64)
    signature = models.CharField(max_length=128)
    key_id = models.CharField(max_length=16)
    public_key = models.CharField(max_length=64)
    created_by_id = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        ordering: ClassVar[list[str]] = ["event_id", "number"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["event", "number"], name="snapshot_number_per_event")
        ]

    def __str__(self) -> str:
        return f"{self.event_id} results #{self.number}"
