from django.apps import AppConfig


class OpsConfig(AppConfig):
    name = "apps.ops"
    label = "ops"
    verbose_name = "Operations: backups, health report, metrics"
