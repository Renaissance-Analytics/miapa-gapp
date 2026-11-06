from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('agent_accounts', '0008_conversation_failure')]
    operations = [migrations.AddField(model_name='uicomposition', name='form_drafts',
        field=models.JSONField(default=dict, editable=False))]
