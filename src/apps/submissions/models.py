from typing import ClassVar

from django.db import models
from django.db.models import Q

from core import clock
from core.ids import new_id


def _project_id() -> str:
    return new_id("prj")


def _version_id() -> str:
    return new_id("ver")


def _answer_id() -> str:
    return new_id("ans")


def _upload_id() -> str:
    return new_id("upl")


class ProjectStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    WITHDRAWN = "withdrawn", "Withdrawn"
    DISQUALIFIED = "disqualified", "Disqualified"


class Project(models.Model):
    """A team's entry. Its content lives in versions; ``canonical_version`` is the one judged.

    Editing a submitted project before the deadline creates a new version, so a
    resubmission (like the fixture's duplicate "Dry Harbour") is one project
    with two versions, never two competing entries.
    """

    id = models.CharField(primary_key=True, max_length=40, default=_project_id, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="projects")
    team = models.ForeignKey("teams.Team", on_delete=models.CASCADE, related_name="projects")
    track = models.ForeignKey(
        "events.Track", on_delete=models.SET_NULL, null=True, blank=True, related_name="projects"
    )
    status = models.CharField(
        max_length=15, choices=ProjectStatus.choices, default=ProjectStatus.DRAFT
    )
    canonical_version = models.ForeignKey(
        "ProjectVersion", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    gallery_order = models.PositiveIntegerField(default=0)
    eligibility = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=clock.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["gallery_order", "created_at"]

    def __str__(self) -> str:
        version = self.canonical_version
        return version.name if version else self.pk

    @property
    def is_public(self) -> bool:
        return self.status == ProjectStatus.SUBMITTED and self.canonical_version_id is not None


class VersionSource(models.TextChoices):
    UI = "ui", "Submission form"
    API = "api", "REST API"
    FIXTURE = "fixture", "Fixture import"
    IMPORT = "import", "Bulk import"


class ProjectVersion(models.Model):
    """One snapshot of a project's content. Immutable once submitted (enforced by a trigger)."""

    id = models.CharField(primary_key=True, max_length=40, default=_version_id, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="versions")
    n = models.PositiveIntegerField()
    name = models.CharField(max_length=200)
    tagline = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    thumbnail = models.FileField(upload_to="thumbnails/", blank=True)
    video_url = models.URLField(max_length=500, blank=True)
    repo_url = models.URLField(max_length=500, blank=True)
    live_url = models.URLField(max_length=500, blank=True)
    tech_tags = models.JSONField(default=list, blank=True)
    source = models.CharField(
        max_length=10, choices=VersionSource.choices, default=VersionSource.UI
    )
    source_ref = models.CharField(max_length=60, blank=True)
    created_at = models.DateTimeField(default=clock.now)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["project_id", "n"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["project", "n"], name="version_number_unique"),
            models.CheckConstraint(condition=Q(n__gte=1), name="version_number_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.name} v{self.n}"

    @property
    def is_draft(self) -> bool:
        return self.submitted_at is None


class ProjectImage(models.Model):
    version = models.ForeignKey(ProjectVersion, on_delete=models.CASCADE, related_name="images")
    file = models.FileField(upload_to="images/")
    alt = models.CharField(max_length=200, blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering: ClassVar[list[str]] = ["order"]

    def __str__(self) -> str:
        return self.alt or self.file.name


class CustomAnswer(models.Model):
    id = models.CharField(primary_key=True, max_length=40, default=_answer_id, editable=False)
    version = models.ForeignKey(ProjectVersion, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(
        "events.CustomQuestion", on_delete=models.CASCADE, related_name="+"
    )
    value = models.TextField(blank=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["version", "question"], name="one_answer_per_question")
        ]

    def __str__(self) -> str:
        return self.value[:40]


class ArtifactUpload(models.Model):
    """A required artifact supplied with a version, plus what its parser read from it."""

    id = models.CharField(primary_key=True, max_length=40, default=_upload_id, editable=False)
    version = models.ForeignKey(ProjectVersion, on_delete=models.CASCADE, related_name="artifacts")
    artifact = models.ForeignKey(
        "events.RequiredArtifact", on_delete=models.CASCADE, related_name="uploads"
    )
    file = models.FileField(upload_to="artifacts/", blank=True)
    url = models.URLField(max_length=500, blank=True)
    text = models.TextField(blank=True)
    parsed = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["version", "artifact"], name="one_upload_per_artifact")
        ]

    def __str__(self) -> str:
        return f"{self.artifact} for {self.version}"
