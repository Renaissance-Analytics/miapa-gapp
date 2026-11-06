import django.db.models.deletion
from django.conf import settings
from django.db import migrations,models

def preserve_drafts(apps,schema_editor):
    if apps.get_model('agent_accounts','ChatDraftRecord').objects.exists():
        raise RuntimeError('Saved chat drafts require offline backup recovery')

class Migration(migrations.Migration):
    dependencies=[('agent_accounts','0009_uicomposition_form_drafts'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[migrations.CreateModel(name='ChatDraftRecord',fields=[
        ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
        ('storage_id',models.UUIDField()),('scope',models.CharField(max_length=36)),
        ('revision',models.PositiveIntegerField(default=0)),('draft',models.JSONField()),('pending',models.JSONField(null=True)),
        ('user',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,to=settings.AUTH_USER_MODEL)),
        ('installation',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,to='workspace.installation')),
        ('account',models.ForeignKey(null=True,on_delete=django.db.models.deletion.PROTECT,to='agent_accounts.agentaccount'))],
        options={'constraints':[models.UniqueConstraint(fields=('user','scope'),name='agent_draft_owned_scope'),
            models.CheckConstraint(condition=models.Q(revision__lt=2_000_000_000),name='agent_draft_revision')]}),
        migrations.RunPython(migrations.RunPython.noop,preserve_drafts)]
