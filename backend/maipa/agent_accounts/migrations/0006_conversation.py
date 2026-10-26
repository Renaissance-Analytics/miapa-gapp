import uuid
from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):
    dependencies = [('agent_accounts', '0005_uicomposition')]
    operations = [migrations.CreateModel(name='Conversation', fields=[
        ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
        ('revision', models.PositiveIntegerField(default=0)),
        ('messages', models.JSONField(default=list)), ('requests', models.JSONField(default=list)),
        ('phase', models.CharField(max_length=16, default='offline')),
        ('worker', models.UUIDField(null=True, default=None, editable=False)),
        ('heartbeat', models.DateTimeField(null=True, editable=False)),
        ('account', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, to='agent_accounts.agentaccount')),
        ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL, editable=False)),
        ('installation', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='workspace.installation', editable=False)),
        ('storage_id', models.UUIDField(editable=False)),
        ('ui', models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, to='agent_accounts.uicomposition')),
    ])]
