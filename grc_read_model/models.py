import uuid

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone


class GRCEntityType(models.TextChoices):
    COUNTRY = "country", "Country"
    DISTRICT = "district", "District"
    DISASTER_EVENT = "disaster_event", "Disaster event"
    OPERATION = "operation", "Operation"
    PROJECT = "project", "Project"
    ACTIVITY = "activity", "Activity"
    FUNDING = "funding", "Funding"
    INDICATOR_VALUE = "indicator_value", "Indicator value"


class GRCSyncRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pipeline = models.CharField(max_length=128)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING, db_index=True)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    source_watermark_from = models.DateTimeField(null=True, blank=True)
    source_watermark_to = models.DateTimeField(null=True, blank=True)
    rows_seen = models.PositiveBigIntegerField(default=0)
    rows_published = models.PositiveBigIntegerField(default=0)
    rows_deleted = models.PositiveBigIntegerField(default=0)
    error_message = models.TextField(blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "grc_sync_run"
        ordering = ("-started_at",)

    def __str__(self):
        return f"{self.pipeline}: {self.status} ({self.id})"


class GRCReadModelState(models.Model):
    source_system = models.CharField(max_length=128)
    stream = models.CharField(max_length=128)
    last_successful_watermark = models.DateTimeField(null=True, blank=True)
    last_successful_run = models.ForeignKey(
        GRCSyncRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="published_states",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "grc_read_model_state"
        constraints = (
            models.UniqueConstraint(
                fields=("source_system", "stream"),
                name="grc_read_model_state_source_stream_uniq",
            ),
        )
        ordering = ("source_system", "stream")

    def __str__(self):
        return f"{self.source_system}: {self.stream}"


class GRCSourceRecord(models.Model):
    source_system = models.CharField(max_length=128)
    entity_type = models.CharField(max_length=32, choices=GRCEntityType.choices)
    source_id = models.CharField(max_length=255)
    source_ingested_at = models.DateTimeField(null=True, blank=True)
    content_hash = models.CharField(max_length=64, blank=True)
    is_deleted = models.BooleanField(default=False)

    target_content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    target_object_id = models.PositiveBigIntegerField()
    target_object = GenericForeignKey("target_content_type", "target_object_id")

    last_successful_run = models.ForeignKey(
        GRCSyncRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="source_records",
    )
    published_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "grc_source_record"
        constraints = (
            models.UniqueConstraint(
                fields=("source_system", "entity_type", "source_id"),
                name="grc_source_record_source_identity_uniq",
            ),
            models.UniqueConstraint(
                fields=(
                    "source_system",
                    "entity_type",
                    "target_content_type",
                    "target_object_id",
                ),
                name="grc_source_record_target_identity_uniq",
            ),
        )
        indexes = (
            models.Index(
                fields=("target_content_type", "target_object_id"),
                name="grc_src_target_idx",
            ),
            models.Index(
                fields=("entity_type", "source_ingested_at"),
                name="grc_src_ingested_idx",
            ),
        )
        ordering = ("source_system", "entity_type", "source_id")

    def __str__(self):
        return f"{self.source_system}: {self.entity_type}/{self.source_id}"
