import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0008_row_matches"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sourcerowmatch",
            name="journal",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="matches", to="ledger.journal"),
        ),
        migrations.AddField(
            model_name="sourcerowmatch",
            name="duplicate_of",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="duplicates", to="ledger.sourcerow"),
        ),
        migrations.AddConstraint(
            model_name="sourcerowmatch",
            constraint=models.CheckConstraint(
                condition=models.Q(("duplicate_of__isnull", True), ("journal__isnull", False))
                | models.Q(("duplicate_of__isnull", False), ("journal__isnull", True)),
                name="ledger_row_match_one_kind",
            ),
        ),
    ]
