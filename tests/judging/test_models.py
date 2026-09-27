import pytest
from django.db import IntegrityError, transaction

from apps.accounts.models import User
from apps.events.models import Criterion, Event
from apps.judging.models import Assignment, Review, ScoreItem
from apps.submissions.models import Project, ProjectVersion
from apps.teams.models import Team
from core import clock


@pytest.fixture
def setup(db) -> dict:
    event = Event.objects.create(slug="hack", name="Hack")
    team = Team.objects.create(event=event, name="T")
    project = Project.objects.create(event=event, team=team)
    v1 = ProjectVersion.objects.create(project=project, n=1, name="A", submitted_at=clock.now())
    v2 = ProjectVersion.objects.create(project=project, n=2, name="A", submitted_at=clock.now())
    judge = User.objects.create_user("jdg@example.org")
    criterion = Criterion.objects.create(event=event, key="quality", label="Quality")
    assignment = Assignment.objects.create(
        event=event, judge=judge, project=project, source="batch"
    )
    return locals()


def test_one_assignment_per_judge_and_project(setup: dict) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        Assignment.objects.create(
            event=setup["event"], judge=setup["judge"], project=setup["project"], source="auto"
        )


def test_a_judge_may_review_each_version_once(setup: dict) -> None:
    common = {
        "assignment": setup["assignment"],
        "event": setup["event"],
        "judge": setup["judge"],
        "project": setup["project"],
    }
    Review.objects.create(version=setup["v1"], **common)
    Review.objects.create(version=setup["v2"], **common)  # a resubmission can be re-reviewed
    with pytest.raises(IntegrityError), transaction.atomic():
        Review.objects.create(version=setup["v2"], **common)


def test_one_score_per_criterion_per_review(setup: dict) -> None:
    review = Review.objects.create(
        assignment=setup["assignment"],
        event=setup["event"],
        judge=setup["judge"],
        project=setup["project"],
        version=setup["v1"],
    )
    ScoreItem.objects.create(review=review, criterion=setup["criterion"], value=4)
    with pytest.raises(IntegrityError), transaction.atomic():
        ScoreItem.objects.create(review=review, criterion=setup["criterion"], value=5)
