from django.db import migrations

from core.dbguards import append_only

forward, reverse = append_only("judging_credential")


class Migration(migrations.Migration):
    dependencies = [
        ("judging", "0012_credentials"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
