from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('agent_accounts', '0007_provider_selection')]
    operations = [migrations.AddField(model_name='conversation', name='failure',
        field=models.JSONField(default=None, editable=False, null=True))]
