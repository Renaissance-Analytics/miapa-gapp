from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


def initialize_bootstrap(apps, schema_editor):
    bootstrap = apps.get_model('workspace', 'BootstrapState')
    installation = apps.get_model('workspace', 'Installation')
    user_app, user_model = settings.AUTH_USER_MODEL.split('.')
    user = apps.get_model(user_app, user_model)
    bootstrap.objects.create(slot=1, closed=installation.objects.exists() or user.objects.exists())


class Migration(migrations.Migration):
    dependencies = [('workspace', '0001_initial'), ('intake', '0001_initial'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='BootstrapState', fields=[
            ('slot', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
            ('closed', models.BooleanField(default=False)),
        ], options={'constraints': [models.CheckConstraint(condition=models.Q(slot=1), name='workspace_single_bootstrap')]}),
        migrations.RunPython(initialize_bootstrap, migrations.RunPython.noop),
        migrations.CreateModel(name='AccountOnboarding', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('identity', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
            ('revision', models.PositiveIntegerField(default=0)),
            ('step', models.CharField(default='display_name', max_length=24)),
            ('draft', models.JSONField(default=dict)), ('messages', models.JSONField(default=list)),
            ('deferred', models.BooleanField(default=False)),
            ('entity', models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, to='intake.entity')),
            ('installation', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='workspace.installation')),
            ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ], options={'constraints': [models.CheckConstraint(condition=models.Q(revision__lt=2_000_000_000), name='workspace_setup_revision')]}),
    ]
