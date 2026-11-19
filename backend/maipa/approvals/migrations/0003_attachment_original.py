from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("approvals", "0002_chat_attachments"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatattachment",
            name="original",
            field=models.BinaryField(blank=True, null=True),
        ),
    ]
