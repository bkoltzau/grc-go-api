from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("grc_read_model", "0001_initial"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="grcsourcerecord",
            constraint=models.UniqueConstraint(
                fields=(
                    "source_system",
                    "entity_type",
                    "target_content_type",
                    "target_object_id",
                ),
                name="grc_source_record_target_identity_uniq",
            ),
        ),
    ]
