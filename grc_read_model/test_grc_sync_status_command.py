import json
from datetime import datetime, timezone
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from grc_read_model.models import GRCReadModelState, GRCSyncRun


class GRCSyncStatusCommandTest(TestCase):
    def setUp(self):
        watermark = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)
        self.run = GRCSyncRun.objects.create(
            pipeline="grc_reference",
            status=GRCSyncRun.Status.SUCCEEDED,
            completed_at=watermark,
            source_watermark_to=watermark,
            rows_seen=4,
            rows_published=3,
        )
        GRCReadModelState.objects.create(
            source_system="grc_gold",
            stream="country_district_event",
            last_successful_watermark=watermark,
            last_successful_run=self.run,
        )

    def test_emits_machine_readable_status(self):
        stdout = StringIO()

        call_command("grc_sync_status", "--json", stdout=stdout)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["states"][0]["source_system"], "grc_gold")
        self.assertEqual(payload["states"][0]["last_successful_run_id"], str(self.run.pk))
        self.assertEqual(payload["runs"][0]["pipeline"], "grc_reference")
        self.assertEqual(payload["runs"][0]["rows_published"], 3)

    def test_emits_human_readable_status(self):
        stdout = StringIO()

        call_command("grc_sync_status", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("GRC read-model states: 1", output)
        self.assertIn("grc_gold/country_district_event", output)
        self.assertIn("grc_reference succeeded", output)

    def test_rejects_unsafe_run_limit(self):
        with self.assertRaisesRegex(CommandError, "between 1 and 100"):
            call_command("grc_sync_status", limit=0)
