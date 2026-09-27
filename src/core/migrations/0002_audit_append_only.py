from django.db import migrations

from core.dbguards import append_only

forward, reverse = append_only("core_auditevent")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_audit_event"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
