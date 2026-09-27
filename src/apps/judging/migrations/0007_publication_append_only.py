from django.db import migrations

from core.dbguards import append_only

forward, reverse = append_only("judging_publication")


class Migration(migrations.Migration):
    dependencies = [
        ("judging", "0006_publication"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
