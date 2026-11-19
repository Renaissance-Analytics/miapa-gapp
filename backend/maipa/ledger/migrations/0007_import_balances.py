from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0006_import_bank_account"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourceimport",
            name="closing_units",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sourceimport",
            name="opening_units",
            field=models.BigIntegerField(blank=True, null=True),
        ),
    ]
