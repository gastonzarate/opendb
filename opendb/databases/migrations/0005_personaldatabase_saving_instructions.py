from django.core.validators import MaxLengthValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("databases", "0004_accessrole_description_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="personaldatabase",
            name="saving_instructions",
            field=models.TextField(
                blank=True, default="", validators=[MaxLengthValidator(4000)]
            ),
        ),
        migrations.AddField(
            model_name="personaldatabase",
            name="saving_instructions_updated_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
    ]
