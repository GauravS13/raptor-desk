from django.apps import AppConfig


class JudgingConfig(AppConfig):
    name = "apps.judging"
    label = "judging"
    verbose_name = "Judge assignment, reviews and scores"

    def ready(self) -> None:
        from apps.judging import publishing  # noqa: F401  (registers publish checks and hooks)
