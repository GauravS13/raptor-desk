from django.db import migrations

from core.dbguards import append_only

forward, reverse = append_only("judging_pairwisecomparison")


class Migration(migrations.Migration):
    dependencies = [
        ("judging", "0015_pairwise"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
