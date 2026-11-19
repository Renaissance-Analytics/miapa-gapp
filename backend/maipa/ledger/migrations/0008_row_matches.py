import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0007_import_balances"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SourceRowMatch",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("journal", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="matches", to="ledger.journal")),
                ("matched_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ("row", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="match", to="ledger.sourcerow")),
            ],
            options={"abstract": False},
        ),
    ]
