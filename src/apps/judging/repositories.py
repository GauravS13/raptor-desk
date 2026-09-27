"""Every read of review data goes through here, already scoped to what the caller may see.

Views never query reviews directly. A judge's queries are filtered to their
own reviews at this layer, so a forgotten check in a view cannot leak a
peer's scores.
"""

from django.db.models import QuerySet

from apps.judging.models import Review
from core.http import forbidden
from core.policy import Principal


def reviews_for_self(principal: Principal, event_id: str | None = None) -> QuerySet[Review]:
    if not principal.user_id:
        raise forbidden()
    reviews = Review.objects.filter(judge_id=principal.user_id)
    if event_id:
        reviews = reviews.filter(event_id=event_id)
    return _with_details(reviews)


def reviews_of_judge(
    principal: Principal, judge_id: str, event_id: str | None = None
) -> QuerySet[Review]:
    """A judge's reviews: to that judge, to the organizers of the events concerned, and to admins.

    A peer judge is refused before anything is looked up, so the answer never
    reveals whether the other judge has reviews at all.
    """
    if principal.user_id == judge_id:
        return reviews_for_self(principal, event_id)
    if principal.is_admin:
        reviews = Review.objects.filter(judge_id=judge_id)
    else:
        organized = principal.events_with_role("organizer")
        if not organized or (event_id and event_id not in organized):
            raise forbidden("Judges can only read their own scores.")
        reviews = Review.objects.filter(judge_id=judge_id, event_id__in=organized)
    if event_id:
        reviews = reviews.filter(event_id=event_id)
    return _with_details(reviews)


def reviews_for_organizer(event_id: str) -> QuerySet[Review]:
    """All reviews of an event. Callers must already hold the event's organizer policy."""
    return _with_details(Review.objects.filter(event_id=event_id))


def _with_details(reviews: QuerySet[Review]) -> QuerySet[Review]:
    return (
        reviews.select_related("project", "version", "judge", "event")
        .prefetch_related("scores__criterion")
        .order_by("event_id", "project_id", "judge_id", "version__n")
    )
