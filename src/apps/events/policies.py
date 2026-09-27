"""Access rules for event configuration. Shared by the pages and the API."""

from core.policy import Rule, define

EVENTS_LIST = define(
    "events.list", Rule(public=True, description="Anyone can list published events.")
)
EVENTS_VIEW = define(
    "events.view",
    Rule(public=True, description="Anyone can view a non-draft event; drafts need an organizer."),
)
EVENTS_CREATE = define(
    "events.create",
    Rule(
        roles=frozenset({"organizer", "admin"}),
        event_scoped=False,
        description="Admins and existing organizers can create events.",
    ),
)
EVENTS_MANAGE = define(
    "events.manage",
    Rule(
        roles=frozenset({"organizer", "admin"}),
        description="Only this event's organizers (or an admin) can change it.",
    ),
)
TEMPLATES_LIST = define(
    "events.templates",
    Rule(authenticated=True, description="Signed-in users can browse templates."),
)
