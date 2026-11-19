import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0010_row_settlements"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Reconciliation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("as_of", models.DateField()),
                ("closing_units", models.BigIntegerField()),
                ("book_units", models.BigIntegerField()),
                ("unrecorded_units", models.BigIntegerField()),
                ("outstanding_units", models.BigIntegerField()),
                ("figures_hash", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="ledger.account")),
                ("recorded_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ("statement", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="reconciliation", to="ledger.sourceimport")),
            ],
            options={"abstract": False},
        ),
    ]
