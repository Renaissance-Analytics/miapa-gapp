import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0009_duplicate_rows"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sourcerowmatch",
            name="row",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="settlements", to="ledger.sourcerow"),
        ),
    ]
