from django.db import migrations

from core.dbguards import append_only

forward, reverse = append_only("judging_resultssnapshot")


class Migration(migrations.Migration):
    dependencies = [
        ("judging", "0004_results_snapshot"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
