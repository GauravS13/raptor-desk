from django.apps import AppConfig


class EventsConfig(AppConfig):
    name = "apps.events"
    label = "events"
    verbose_name = "Events, rubrics and lifecycle"

    def ready(self) -> None:
        from apps.events import preflight  # noqa: F401  (registers preflight checks)
