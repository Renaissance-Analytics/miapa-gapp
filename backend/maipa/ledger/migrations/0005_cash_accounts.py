from django.db import migrations, models


def mark_starter_checking(apps, schema_editor):
    """Books made before this flag existed: the unrenamed starter checking account is a bank account."""
    apps.get_model('ledger', 'Account').objects.filter(code='1000', name='Checking', category='asset').update(cash=True)


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0004_period_close"),
    ]

    operations = [
        migrations.AddField(
            model_name="account",
            name="cash",
            field=models.BooleanField(default=False),
        ),
        migrations.AddConstraint(
            model_name="account",
            constraint=models.CheckConstraint(
                condition=models.Q(("cash", False), ("category", "asset"), _connector="OR"),
                name="ledger_account_cash_asset",
            ),
        ),
        migrations.RunPython(mark_starter_checking, migrations.RunPython.noop),
    ]
