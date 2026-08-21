from datetime import datetime, timezone
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings


class GRCSyncReferenceCommandTest(SimpleTestCase):
    @override_settings(DJANGO_READ_ONLY=True)
    @patch("grc_read_model.management.commands.grc_sync_reference.load_grc_reference_snapshot")
    def test_rejects_serving_process_read_only_mode(self, load_snapshot):
        with self.assertRaisesRegex(CommandError, "DJANGO_READ_ONLY=false"):
            call_command("grc_sync_reference")

        load_snapshot.assert_not_called()

    @override_settings(DJANGO_READ_ONLY=False)
    @patch("grc_read_model.management.commands.grc_sync_reference.publish_grc_reference_snapshot")
    @patch("grc_read_model.management.commands.grc_sync_reference.load_grc_reference_snapshot")
    @patch("grc_read_model.management.commands.grc_sync_reference.GRCDWHSettings.from_env")
    def test_loads_and_publishes_snapshot_with_explicit_metadata(
        self,
        load_settings,
        load_snapshot,
        publish_snapshot,
    ):
        dwh_settings = Mock()
        snapshot = Mock()
        watermark = datetime(2026, 8, 20, tzinfo=timezone.utc)
        sync_run = SimpleNamespace(
            pk="run-id",
            rows_seen=4,
            rows_published=3,
            rows_deleted=0,
            source_watermark_to=watermark,
        )
        load_settings.return_value = dwh_settings
        load_snapshot.return_value = snapshot
        publish_snapshot.return_value = sync_run
        stdout = StringIO()

        call_command(
            "grc_sync_reference",
            source_system="test_gold",
            stream="reference",
            pipeline="manual_test",
            stdout=stdout,
        )

        load_snapshot.assert_called_once_with(dwh_settings)
        publish_snapshot.assert_called_once_with(
            snapshot,
            source_system="test_gold",
            stream="reference",
            pipeline="manual_test",
        )
        self.assertIn("run-id succeeded", stdout.getvalue())
        self.assertIn("watermark=2026-08-20T00:00:00+00:00", stdout.getvalue())
