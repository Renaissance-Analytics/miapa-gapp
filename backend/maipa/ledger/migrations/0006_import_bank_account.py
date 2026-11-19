import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0005_cash_accounts"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourceimport",
            name="account",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="ledger.account"),
        ),
    ]
