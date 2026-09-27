import pytest
from django.db import DatabaseError, IntegrityError, transaction

from apps.events.models import Event
from apps.submissions.models import Project, ProjectVersion
from apps.teams.models import Team
from core import clock


@pytest.fixture
def project(db) -> Project:
    event = Event.objects.create(slug="hack", name="Hack")
    team = Team.objects.create(event=event, name="NorthKiln")
    return Project.objects.create(event=event, team=team)


def test_draft_versions_can_change(project: Project) -> None:
    draft = ProjectVersion.objects.create(project=project, n=1, name="Glass Signal")
    ProjectVersion.objects.filter(pk=draft.pk).update(tagline="Now with a tagline")
    draft.refresh_from_db()
    assert draft.tagline == "Now with a tagline"


def test_submitted_versions_are_immutable_in_the_database(project: Project) -> None:
    version = ProjectVersion.objects.create(
        project=project, n=1, name="Glass Signal", submitted_at=clock.now()
    )
    with pytest.raises(DatabaseError), transaction.atomic():
        ProjectVersion.objects.filter(pk=version.pk).update(name="Rewritten after submission")
    version.refresh_from_db()
    assert version.name == "Glass Signal"


def test_version_numbers_are_unique_per_project(project: Project) -> None:
    ProjectVersion.objects.create(project=project, n=1, name="A")
    with pytest.raises(IntegrityError), transaction.atomic():
        ProjectVersion.objects.create(project=project, n=1, name="B")


def test_project_is_public_only_when_submitted(project: Project) -> None:
    version = ProjectVersion.objects.create(
        project=project, n=1, name="A", submitted_at=clock.now()
    )
    assert not project.is_public
    project.canonical_version = version
    project.status = "submitted"
    project.save()
    assert project.is_public
