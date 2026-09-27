from django.db import migrations

from core.dbguards import frozen_after

forward, reverse = frozen_after("submissions_projectversion", "submitted_at")


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
