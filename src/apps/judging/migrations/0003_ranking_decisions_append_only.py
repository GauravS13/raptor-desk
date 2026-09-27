from django.db import migrations

from core.dbguards import append_only

forward, reverse = append_only("judging_rankingdecision")


class Migration(migrations.Migration):
    dependencies = [
        ("judging", "0002_ranking_decision"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
