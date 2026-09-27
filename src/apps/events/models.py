from decimal import Decimal
from typing import ClassVar

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q

from core import clock
from core.ids import new_id


def _event_id() -> str:
    return new_id("evt")


def _track_id() -> str:
    return new_id("trk")


def _prize_id() -> str:
    return new_id("prz")


def _criterion_id() -> str:
    return new_id("crt")


def _question_id() -> str:
    return new_id("qst")


def _transition_id() -> str:
    return new_id("phs")


class Phase(models.TextChoices):
    DRAFT = "draft", "Draft"
    REGISTRATION = "registration", "Registration"
    SUBMISSIONS = "submissions", "Submissions open"
    ELIGIBILITY = "eligibility", "Eligibility review"
    JUDGING = "judging", "Judging"
    DELIBERATION = "deliberation", "Deliberation"
    PUBLISHED = "published", "Results published"
    ARCHIVED = "archived", "Archived"


PHASE_ORDER = [choice.value for choice in Phase]


class Weighting(models.TextChoices):
    PERCENT = "percent", "Percent weights (sum to 100)"
    MULTIPLIER = "multiplier", "Relative multipliers"


class VotingMode(models.TextChoices):
    OFF = "off", "No public vote"
    LINK = "link", "Link-based ballot tokens"
    EMAIL = "email", "Email-verified voters"
    AUTH = "auth", "Signed-in participants"


class VotingScheme(models.TextChoices):
    SINGLE = "single", "One vote per voter"
    QUADRATIC = "quadratic", "Quadratic (credits)"


class Event(models.Model):
    id = models.CharField(primary_key=True, max_length=40, default=_event_id, editable=False)
    slug = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    phase = models.CharField(max_length=20, choices=Phase.choices, default=Phase.DRAFT)
    template_key = models.CharField(max_length=60, blank=True)

    registration_opens_at = models.DateTimeField(null=True, blank=True)
    registration_closes_at = models.DateTimeField(null=True, blank=True)
    submissions_open_at = models.DateTimeField(null=True, blank=True)
    submissions_close_at = models.DateTimeField(null=True, blank=True)
    judging_closes_at = models.DateTimeField(null=True, blank=True)
    results_published_at = models.DateTimeField(null=True, blank=True)

    weighting = models.CharField(
        max_length=12, choices=Weighting.choices, default=Weighting.PERCENT
    )
    reviews_per_project = models.PositiveSmallIntegerField(default=3)
    max_load_per_judge = models.PositiveSmallIntegerField(default=12)
    blind_judging = models.BooleanField(default=False)
    max_team_size = models.PositiveSmallIntegerField(default=4)
    allow_multiple_projects = models.BooleanField(default=False)

    voting_mode = models.CharField(
        max_length=10, choices=VotingMode.choices, default=VotingMode.OFF
    )
    voting_scheme = models.CharField(
        max_length=10, choices=VotingScheme.choices, default=VotingScheme.SINGLE
    )
    qv_credits = models.PositiveSmallIntegerField(default=9)
    voting_opens_at = models.DateTimeField(null=True, blank=True)
    voting_closes_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(default=clock.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(submissions_open_at__isnull=True)
                | Q(submissions_close_at__isnull=True)
                | Q(submissions_open_at__lt=F("submissions_close_at")),
                name="event_submission_window_ordered",
            ),
            models.CheckConstraint(
                condition=Q(voting_opens_at__isnull=True)
                | Q(voting_closes_at__isnull=True)
                | Q(voting_opens_at__lt=F("voting_closes_at")),
                name="event_voting_window_ordered",
            ),
            models.CheckConstraint(
                condition=Q(reviews_per_project__gte=1), name="event_reviews_per_project_positive"
            ),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def submissions_are_open(self) -> bool:
        now = clock.now()
        if self.submissions_close_at is None or now >= self.submissions_close_at:
            return False
        return self.submissions_open_at is None or now >= self.submissions_open_at

    @property
    def voting_is_open(self) -> bool:
        if self.voting_mode == VotingMode.OFF or not self.voting_opens_at:
            return False
        now = clock.now()
        return self.voting_opens_at <= now and (
            self.voting_closes_at is None or now < self.voting_closes_at
        )

    @property
    def results_are_published(self) -> bool:
        return self.results_published_at is not None


class Track(models.Model):
    id = models.CharField(primary_key=True, max_length=40, default=_track_id, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="tracks")
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering: ClassVar[list[str]] = ["order", "name"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["event", "name"], name="track_name_unique_per_event")
        ]

    def __str__(self) -> str:
        return self.name


class Prize(models.Model):
    id = models.CharField(primary_key=True, max_length=40, default=_prize_id, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="prizes")
    track = models.ForeignKey(Track, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=120)
    rank = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    amount = models.CharField(max_length=60, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["rank", "name"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(condition=Q(rank__gte=1), name="prize_rank_positive")
        ]

    def __str__(self) -> str:
        return f"{self.rank}. {self.name}"


class Criterion(models.Model):
    """One rubric criterion. Weights are percent or multipliers, per the event's weighting.

    ``is_gate``: a project below ``gate_threshold`` is not scored (DOGFOOD's T1 rule).
    ``is_bonus``: never added to the score; only breaks ties.
    """

    id = models.CharField(primary_key=True, max_length=40, default=_criterion_id, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="criteria")
    key = models.SlugField(max_length=60)
    label = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    weight = models.DecimalField(max_digits=7, decimal_places=3, default=Decimal("1"))
    min_score = models.SmallIntegerField(default=1)
    max_score = models.SmallIntegerField(default=5)
    anchors = models.JSONField(default=dict, blank=True)
    is_gate = models.BooleanField(default=False)
    gate_threshold = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)
    is_bonus = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering: ClassVar[list[str]] = ["order", "key"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["event", "key"], name="criterion_key_unique_per_event"),
            models.CheckConstraint(
                condition=Q(min_score__lt=F("max_score")), name="criterion_range"
            ),
            models.CheckConstraint(
                condition=Q(weight__gte=0), name="criterion_weight_non_negative"
            ),
            models.CheckConstraint(
                condition=Q(is_gate=False) | Q(gate_threshold__isnull=False),
                name="criterion_gate_has_threshold",
            ),
        ]

    def __str__(self) -> str:
        return self.label


class QuestionKind(models.TextChoices):
    TEXT = "text", "Short text"
    LONG_TEXT = "long_text", "Long text"
    URL = "url", "Link"
    CHOICE = "choice", "Choice"
    BOOL = "bool", "Yes / no"


class CustomQuestion(models.Model):
    """An organizer-defined question on the submission form."""

    id = models.CharField(primary_key=True, max_length=40, default=_question_id, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="questions")
    label = models.CharField(max_length=200)
    help_text = models.CharField(max_length=300, blank=True)
    kind = models.CharField(max_length=12, choices=QuestionKind.choices, default=QuestionKind.TEXT)
    choices = models.JSONField(default=list, blank=True)
    required = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering: ClassVar[list[str]] = ["order", "label"]

    def __str__(self) -> str:
        return self.label


class PhaseTransition(models.Model):
    """Append-only history of an event's phase changes, with the preflight report."""

    id = models.CharField(primary_key=True, max_length=40, default=_transition_id, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="transitions")
    from_phase = models.CharField(max_length=20, choices=Phase.choices)
    to_phase = models.CharField(max_length=20, choices=Phase.choices)
    actor_id = models.CharField(max_length=40, blank=True)
    reason = models.TextField(blank=True)
    overridden = models.BooleanField(default=False)
    preflight = models.JSONField(default=list, blank=True)
    at = models.DateTimeField(default=clock.now)

    class Meta:
        ordering: ClassVar[list[str]] = ["-at"]

    def __str__(self) -> str:
        return f"{self.event_id}: {self.from_phase} -> {self.to_phase}"
