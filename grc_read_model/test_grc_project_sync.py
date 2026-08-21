from datetime import datetime, timezone
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from grc_read_model.grc_project_projection import GRCGoldProjectDeletion, GRCProjectPublicationResult
from grc_read_model.grc_project_sync import (
    GRCGoldProjectSnapshot,
    GRCProjectSyncError,
    publish_grc_project_snapshot,
)
from grc_read_model.models import GRCReadModelState, GRCSyncRun


def empty_snapshot(watermark):
    return GRCGoldProjectSnapshot(
        sectors=[],
        projects=[],
        deletions=[],
        watermark=watermark,
    )


class GRCGoldProjectSnapshotTest(SimpleTestCase):
    def test_freezes_sequences_and_requires_aware_watermark(self):
        projects = []
        snapshot = GRCGoldProjectSnapshot(
            sectors=[],
            projects=projects,
            deletions=[],
            watermark=datetime(2026, 8, 21, tzinfo=timezone.utc),
        )
        projects.append(object())

        self.assertEqual(snapshot.projects, ())
        with self.assertRaisesRegex(GRCProjectSyncError, "timezone-aware"):
            empty_snapshot(datetime(2026, 8, 21))

    def test_rejects_ingestion_timestamp_after_transaction_watermark(self):
        watermark = datetime(2026, 8, 21, tzinfo=timezone.utc)

        with self.assertRaisesRegex(GRCProjectSyncError, "later than the Gold transaction watermark"):
            GRCGoldProjectSnapshot(
                sectors=[],
                projects=[],
                deletions=[
                    GRCGoldProjectDeletion(
                        project_id=7001,
                        ingested_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
                    )
                ],
                watermark=watermark,
            )

        with self.assertRaisesRegex(GRCProjectSyncError, "invalid ingestion timestamps"):
            GRCGoldProjectSnapshot(
                sectors=[],
                projects=[],
                deletions=[
                    GRCGoldProjectDeletion(
                        project_id=7001,
                        ingested_at=datetime(2026, 8, 20),
                    )
                ],
                watermark=watermark,
            )


class GRCProjectSyncTest(TestCase):
    @patch("grc_read_model.grc_project_sync.publish_grc_project_records")
    @patch("grc_read_model.grc_project_sync.validate_grc_project_sectors")
    def test_advances_watermark_only_with_successful_publication(self, validate_sectors, publish_records):
        publish_records.return_value = GRCProjectPublicationResult(
            rows_seen=3,
            rows_created=2,
            rows_updated=0,
            rows_deleted=1,
        )
        watermark = datetime(2026, 8, 21, tzinfo=timezone.utc)

        sync_run = publish_grc_project_snapshot(empty_snapshot(watermark))

        self.assertEqual(sync_run.status, GRCSyncRun.Status.SUCCEEDED)
        self.assertEqual(sync_run.rows_seen, 3)
        self.assertEqual(sync_run.rows_published, 0)
        self.assertEqual(sync_run.rows_deleted, 1)
        self.assertEqual(sync_run.details["projects"]["rows_created"], 2)
        state = GRCReadModelState.objects.get(source_system="grc_gold", stream="project")
        self.assertEqual(state.last_successful_watermark, watermark)
        self.assertEqual(state.last_successful_run, sync_run)
        validate_sectors.assert_called_once_with(())
        publish_records.assert_called_once()

    @patch("grc_read_model.grc_project_sync.publish_grc_project_records")
    @patch("grc_read_model.grc_project_sync.validate_grc_project_sectors")
    def test_failure_preserves_last_successful_state(self, validate_sectors, publish_records):
        first_watermark = datetime(2026, 8, 20, tzinfo=timezone.utc)
        next_watermark = datetime(2026, 8, 21, tzinfo=timezone.utc)
        result = GRCProjectPublicationResult(
            rows_seen=0,
            rows_created=0,
            rows_updated=0,
            rows_deleted=0,
        )
        publish_records.return_value = result
        successful_run = publish_grc_project_snapshot(empty_snapshot(first_watermark))
        publish_records.side_effect = RuntimeError("publication failed")

        with self.assertRaisesRegex(RuntimeError, "publication failed"):
            publish_grc_project_snapshot(empty_snapshot(next_watermark))

        state = GRCReadModelState.objects.get(source_system="grc_gold", stream="project")
        self.assertEqual(state.last_successful_watermark, first_watermark)
        self.assertEqual(state.last_successful_run, successful_run)
        failed_run = GRCSyncRun.objects.get(status=GRCSyncRun.Status.FAILED)
        self.assertEqual(failed_run.source_watermark_from, first_watermark)
        self.assertEqual(failed_run.source_watermark_to, next_watermark)
        self.assertEqual(failed_run.details, {"error_type": "RuntimeError"})

    @patch("grc_read_model.grc_project_sync.publish_grc_project_records")
    @patch("grc_read_model.grc_project_sync.validate_grc_project_sectors")
    def test_rejects_regressive_watermark(self, validate_sectors, publish_records):
        publish_records.return_value = GRCProjectPublicationResult(
            rows_seen=0,
            rows_created=0,
            rows_updated=0,
            rows_deleted=0,
        )
        publish_grc_project_snapshot(
            empty_snapshot(datetime(2026, 8, 21, tzinfo=timezone.utc))
        )

        with self.assertRaisesRegex(GRCProjectSyncError, "must not be older"):
            publish_grc_project_snapshot(
                empty_snapshot(datetime(2026, 8, 20, tzinfo=timezone.utc))
            )
