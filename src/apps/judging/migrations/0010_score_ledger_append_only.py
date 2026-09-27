from django.db import migrations

from core.dbguards import append_only

forward, reverse = append_only("judging_scoreevent")


class Migration(migrations.Migration):
    dependencies = [
        ("judging", "0009_score_ledger"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
