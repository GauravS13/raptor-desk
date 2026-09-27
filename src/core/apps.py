from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"
    verbose_name = "Core infrastructure"

    def ready(self) -> None:
        from core import checks  # noqa: F401  (registers system checks)
