from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    name = "apps.integrations"
    label = "integrations"
    verbose_name = "Webhooks, embeddable gallery, event bundles"

    def ready(self) -> None:
        # Registering phase hooks that emit webhook events.
        from apps.integrations import webhooks  # noqa: F401
