from django.db import migrations

from core.dbguards import append_only

forward, reverse = append_only("events_phasetransition")


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
