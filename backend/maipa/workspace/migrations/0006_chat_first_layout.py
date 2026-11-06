from django.db import migrations, models


def update_untouched_default(apps, schema_editor):
    # An explicit prior gesture is preserved, including a chosen 28 percent.
    apps.get_model('workspace', 'WorkspaceLayout').objects.filter(revision=0, split_basis_points=2800).update(split_basis_points=6000)


class Migration(migrations.Migration):
    dependencies = [('workspace', '0005_accessevent_installation_access_revision_and_more')]
    operations = [
        migrations.RemoveConstraint(model_name='workspacelayout', name='workspace_layout_bounds'),
        migrations.AlterField(model_name='workspacelayout', name='split_basis_points', field=models.PositiveSmallIntegerField(default=6000)),
        migrations.AddConstraint(model_name='workspacelayout', constraint=models.CheckConstraint(
            condition=models.Q(split_basis_points__gte=2200, split_basis_points__lte=7200), name='workspace_layout_bounds')),
        migrations.RunPython(update_untouched_default, migrations.RunPython.noop),
    ]
