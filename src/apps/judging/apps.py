from django.apps import AppConfig


class JudgingConfig(AppConfig):
    name = "apps.judging"
    label = "judging"
    verbose_name = "Judge assignment, reviews and scores"

    def ready(self) -> None:
        # Registering preflight checks and phase hooks.
        from apps.judging import credentials, publishing  # noqa: F401
