import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('agent_accounts', '0004_providerprofile')]
    operations = [
        migrations.CreateModel(
            name='UiComposition',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('role', models.CharField(editable=False, max_length=16)),
                ('access_revision', models.PositiveIntegerField(editable=False)),
                ('revision', models.PositiveIntegerField(default=0)),
                ('composition', models.JSONField(default=None, null=True)),
                ('session', models.ForeignKey(editable=False, on_delete=django.db.models.deletion.PROTECT,
                                             to='agent_accounts.sessionreference')),
            ],
            options={'constraints': [
                models.CheckConstraint(condition=models.Q(role__in=('administrator', 'preparer')), name='agent_ui_known_role'),
                models.CheckConstraint(condition=models.Q(revision__lt=2_000_000_000), name='agent_ui_revision_budget'),
            ]},
        ),
    ]
