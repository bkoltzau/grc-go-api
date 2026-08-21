from datetime import datetime, timezone
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings


class GRCSyncProjectsCommandTest(SimpleTestCase):
    @override_settings(DJANGO_READ_ONLY=True)
    @patch("grc_read_model.management.commands.grc_sync_projects.load_grc_project_snapshot")
    def test_rejects_serving_process_read_only_mode(self, load_snapshot):
        with self.assertRaisesRegex(CommandError, "write-enabled publisher"):
            call_command("grc_sync_projects")

        load_snapshot.assert_not_called()

    @override_settings(DJANGO_READ_ONLY=False)
    @patch("grc_read_model.management.commands.grc_sync_projects.publish_grc_project_snapshot")
    @patch("grc_read_model.management.commands.grc_sync_projects.load_grc_project_snapshot")
    @patch("grc_read_model.management.commands.grc_sync_projects.GRCDWHSettings.from_env")
    def test_loads_and_publishes_snapshot_with_explicit_metadata(
        self,
        load_settings,
        load_snapshot,
        publish_snapshot,
    ):
        dwh_settings = Mock()
        snapshot = Mock()
        watermark = datetime(2026, 8, 21, tzinfo=timezone.utc)
        sync_run = SimpleNamespace(
            rows_seen=5,
            rows_published=3,
            rows_deleted=1,
            source_watermark_to=watermark,
        )
        load_settings.return_value = dwh_settings
        load_snapshot.return_value = snapshot
        publish_snapshot.return_value = sync_run
        stdout = StringIO()

        call_command(
            "grc_sync_projects",
            source_system="test_gold",
            stream="project_test",
            pipeline="manual_project_test",
            stdout=stdout,
        )

        load_snapshot.assert_called_once_with(dwh_settings)
        publish_snapshot.assert_called_once_with(
            snapshot,
            source_system="test_gold",
            stream="project_test",
            pipeline="manual_project_test",
        )
        self.assertIn("seen=5", stdout.getvalue())
        self.assertIn("deleted=1", stdout.getvalue())
        self.assertIn("watermark=2026-08-21T00:00:00+00:00", stdout.getvalue())
