from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from grc_read_model.models import (
    GRCEntityType,
    GRCReadModelState,
    GRCSourceRecord,
    GRCSyncRun,
)


class GRCReadModelMetadataTest(TestCase):
    def setUp(self):
        self.run = GRCSyncRun.objects.create(pipeline="gold-to-go")
        self.target = ContentType.objects.get_for_model(GRCSourceRecord)
        self.target_content_type = ContentType.objects.get_for_model(ContentType)

    def create_source_record(self, **kwargs):
        values = {
            "source_system": "grc_gold",
            "entity_type": GRCEntityType.PROJECT,
            "source_id": "project-123",
            "source_ingested_at": timezone.now(),
            "content_hash": "a" * 64,
            "target_content_type": self.target_content_type,
            "target_object_id": self.target.pk,
            "last_successful_run": self.run,
        }
        values.update(kwargs)
        return GRCSourceRecord.objects.create(**values)

    def test_source_record_resolves_target(self):
        source_record = self.create_source_record()

        self.assertEqual(source_record.target_object, self.target)
        self.assertFalse(source_record.is_deleted)

    def test_source_identity_is_unique(self):
        self.create_source_record()

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_source_record(target_object_id=self.target.pk + 1)

    def test_same_source_id_can_be_used_by_different_entity_types(self):
        project_record = self.create_source_record()
        activity_record = self.create_source_record(entity_type=GRCEntityType.ACTIVITY)

        self.assertNotEqual(project_record.pk, activity_record.pk)

    def test_state_is_unique_per_source_stream(self):
        GRCReadModelState.objects.create(
            source_system="grc_gold",
            stream="factproject",
            last_successful_watermark=timezone.now(),
            last_successful_run=self.run,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            GRCReadModelState.objects.create(
                source_system="grc_gold",
                stream="factproject",
            )

    def test_sync_run_starts_in_running_state(self):
        self.assertEqual(self.run.status, GRCSyncRun.Status.RUNNING)
        self.assertEqual(self.run.rows_seen, 0)
        self.assertIsNone(self.run.completed_at)
