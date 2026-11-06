import django.db.models.deletion
from django.db import migrations,models

def preserve_saved_navigation(apps,schema_editor):
    if apps.get_model('workflows','WorkflowSelection').objects.exists():
        raise RuntimeError('Saved workflow navigation requires offline backup recovery')

class Migration(migrations.Migration):
    dependencies=[('workflows','0005_shared_authoring')]
    operations=[migrations.CreateModel(name='WorkflowSelection',fields=[
        ('draft',models.OneToOneField(primary_key=True,serialize=False,on_delete=django.db.models.deletion.PROTECT,to='workflows.workflowdraft')),
        ('run',models.ForeignKey(null=True,on_delete=django.db.models.deletion.PROTECT,to='workflows.workflowrun')),
        ('revision',models.PositiveIntegerField(default=0)),('show_details',models.BooleanField(default=False)),
        ('source',models.CharField(default='human',max_length=8))],options={'constraints':[
            models.CheckConstraint(condition=models.Q(revision__lt=2_000_000_000),name='flow_selection_revision'),
            models.CheckConstraint(condition=models.Q(source__in=('human','agent')),name='flow_selection_source')]}),
        migrations.RunPython(migrations.RunPython.noop,preserve_saved_navigation)]
