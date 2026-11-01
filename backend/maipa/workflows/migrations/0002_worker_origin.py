"""Preserve unverifiable history without granting it a fabricated origin."""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def block_unverified(apps, schema_editor):
    apps.get_model('workflows','WorkflowRun').objects.using(schema_editor.connection.alias).filter(
        status__in=('queued','running')).update(status='blocked',error='origin_unverified')


def refuse_unverified_rollback(apps, schema_editor):
    # Removing original bindings would make definition ownership authoritative
    # again. Roll back from an offline backup instead of transferring authority.
    raise RuntimeError('Workflow origin migration requires offline backup recovery')


class Migration(migrations.Migration):
    dependencies=[('workflows','0001_initial'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[
        migrations.RemoveConstraint(model_name='workflowrun',name='flow_run_status'),
        migrations.AddField(model_name='workflowrun',name='origin_owner',field=models.ForeignKey(
            to=settings.AUTH_USER_MODEL,null=True,editable=False,on_delete=django.db.models.deletion.PROTECT)),
        migrations.AddField(model_name='workflowrun',name='origin_installation',field=models.ForeignKey(
            to='workspace.installation',null=True,editable=False,on_delete=django.db.models.deletion.PROTECT)),
        migrations.AddField(model_name='workflowrun',name='origin_entity',field=models.ForeignKey(
            to='intake.entity',null=True,editable=False,on_delete=django.db.models.deletion.PROTECT)),
        migrations.AddConstraint(model_name='workflowrun',constraint=models.CheckConstraint(
            condition=models.Q(status__in=('queued','running','blocked','completed','failed','cancelled')),name='flow_run_status')),
        migrations.AddIndex(model_name='workflowrun',index=models.Index(fields=['status','created_at','id'],name='flow_dispatch_queue')),
        migrations.RunPython(block_unverified,refuse_unverified_rollback),
    ]
