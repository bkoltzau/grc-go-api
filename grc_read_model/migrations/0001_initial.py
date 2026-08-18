import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.CreateModel(
            name="GRCSyncRun",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("pipeline", models.CharField(max_length=128)),
                (
                    "status",
                    models.CharField(
                        choices=[("running", "Running"), ("succeeded", "Succeeded"), ("failed", "Failed")],
                        db_index=True,
                        default="running",
                        max_length=16,
                    ),
                ),
                ("started_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("source_watermark_from", models.DateTimeField(blank=True, null=True)),
                ("source_watermark_to", models.DateTimeField(blank=True, null=True)),
                ("rows_seen", models.PositiveBigIntegerField(default=0)),
                ("rows_published", models.PositiveBigIntegerField(default=0)),
                ("rows_deleted", models.PositiveBigIntegerField(default=0)),
                ("error_message", models.TextField(blank=True)),
                ("details", models.JSONField(blank=True, default=dict)),
            ],
            options={
                "db_table": "grc_sync_run",
                "ordering": ("-started_at",),
            },
        ),
        migrations.CreateModel(
            name="GRCReadModelState",
            fields=[
                (
                    "id",
                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
                ),
                ("source_system", models.CharField(max_length=128)),
                ("stream", models.CharField(max_length=128)),
                ("last_successful_watermark", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "last_successful_run",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="published_states",
                        to="grc_read_model.grcsyncrun",
                    ),
                ),
            ],
            options={
                "db_table": "grc_read_model_state",
                "ordering": ("source_system", "stream"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("source_system", "stream"),
                        name="grc_read_model_state_source_stream_uniq",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="GRCSourceRecord",
            fields=[
                (
                    "id",
                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
                ),
                ("source_system", models.CharField(max_length=128)),
                (
                    "entity_type",
                    models.CharField(
                        choices=[
                            ("country", "Country"),
                            ("district", "District"),
                            ("disaster_event", "Disaster event"),
                            ("operation", "Operation"),
                            ("project", "Project"),
                            ("activity", "Activity"),
                            ("funding", "Funding"),
                            ("indicator_value", "Indicator value"),
                        ],
                        max_length=32,
                    ),
                ),
                ("source_id", models.CharField(max_length=255)),
                ("source_ingested_at", models.DateTimeField(blank=True, null=True)),
                ("content_hash", models.CharField(blank=True, max_length=64)),
                ("is_deleted", models.BooleanField(default=False)),
                ("target_object_id", models.PositiveBigIntegerField()),
                ("published_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "last_successful_run",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="source_records",
                        to="grc_read_model.grcsyncrun",
                    ),
                ),
                (
                    "target_content_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="contenttypes.contenttype",
                    ),
                ),
            ],
            options={
                "db_table": "grc_source_record",
                "ordering": ("source_system", "entity_type", "source_id"),
                "indexes": [
                    models.Index(
                        fields=["target_content_type", "target_object_id"],
                        name="grc_src_target_idx",
                    ),
                    models.Index(
                        fields=["entity_type", "source_ingested_at"],
                        name="grc_src_ingested_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("source_system", "entity_type", "source_id"),
                        name="grc_source_record_source_identity_uniq",
                    ),
                ],
            },
        ),
    ]
