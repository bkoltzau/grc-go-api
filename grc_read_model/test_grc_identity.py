from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from api.models import Country
from grc_read_model.grc_identity import (
    advance_grc_target_sequence,
    GRCIdentityError,
    grc_source_content_matches,
    parse_grc_source_id,
    publish_grc_source_record,
    resolve_grc_target_id,
    resolve_grc_target_map,
)
from grc_read_model.models import GRCEntityType, GRCSourceRecord, GRCSyncRun


SOURCE_ID = UUID("10000000-0000-0000-0000-000000000001")
OTHER_SOURCE_ID = UUID("10000000-0000-0000-0000-000000000002")


class GRCIdentityTest(TestCase):
    def setUp(self):
        self.sync_run = GRCSyncRun.objects.create(pipeline="identity")
        self.country = Country.objects.create(pk=276, name="Germany", iso="DE", iso3="DEU")

    def publish_mapping(self, source_id=SOURCE_ID, target_object_id=276):
        return publish_grc_source_record(
            source_system="grc_gold",
            entity_type=GRCEntityType.COUNTRY,
            source_id=source_id,
            target_model=Country,
            target_object_id=target_object_id,
            source_ingested_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
            content_hash="a" * 64,
            is_deleted=False,
            sync_run=self.sync_run,
        )

    def test_parses_and_canonicalizes_uuid_source_identity(self):
        self.assertEqual(parse_grc_source_id(str(SOURCE_ID).upper()), SOURCE_ID)
        with self.assertRaisesRegex(GRCIdentityError, "must be a UUID"):
            parse_grc_source_id("project-123")

    @patch("grc_read_model.grc_identity.connection")
    def test_advances_target_sequence_without_reusing_mapped_ids(self, mock_connection):
        self.publish_mapping(target_object_id=900)
        mock_connection.ops.quote_name.side_effect = lambda value: f'"{value}"'
        cursor = mock_connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("public.api_country_id_seq",)

        advance_grc_target_sequence(Country)

        self.assertEqual(cursor.execute.call_count, 2)
        sequence_call = cursor.execute.call_args_list[1]
        self.assertIn("GREATEST(nextval(%s::regclass)", sequence_call.args[0])
        self.assertEqual(
            sequence_call.args[1],
            ["public.api_country_id_seq", "public.api_country_id_seq", 900],
        )

    def test_resolves_preferred_or_allocated_target_identity(self):
        self.assertEqual(
            resolve_grc_target_id(
                source_system="grc_gold",
                entity_type=GRCEntityType.COUNTRY,
                source_id=SOURCE_ID,
                target_model=Country,
                preferred_target_id=276,
            ),
            276,
        )
        self.assertIsNone(
            resolve_grc_target_id(
                source_system="grc_gold",
                entity_type=GRCEntityType.COUNTRY,
                source_id=SOURCE_ID,
                target_model=Country,
                preferred_target_id=None,
            )
        )

        self.publish_mapping()
        self.assertEqual(
            resolve_grc_target_id(
                source_system="grc_gold",
                entity_type=GRCEntityType.COUNTRY,
                source_id=SOURCE_ID,
                target_model=Country,
                preferred_target_id=None,
            ),
            276,
        )

    def test_resolves_required_relationships_and_rejects_deleted_mapping(self):
        source_record = self.publish_mapping()
        self.assertEqual(
            resolve_grc_target_map(
                source_system="grc_gold",
                entity_type=GRCEntityType.COUNTRY,
                source_ids=[SOURCE_ID],
                target_model=Country,
            ),
            {SOURCE_ID: 276},
        )

        source_record.is_deleted = True
        source_record.save(update_fields=("is_deleted",))
        with self.assertRaisesRegex(GRCIdentityError, "marked deleted"):
            resolve_grc_target_map(
                source_system="grc_gold",
                entity_type=GRCEntityType.COUNTRY,
                source_ids=[SOURCE_ID],
                target_model=Country,
            )

    def test_rejects_two_source_guids_for_one_target(self):
        self.publish_mapping()
        with self.assertRaisesRegex(GRCIdentityError, "already mapped"):
            self.publish_mapping(source_id=OTHER_SOURCE_ID)

        target_content_type = ContentType.objects.get_for_model(Country)
        self.assertEqual(
            GRCSourceRecord.objects.filter(target_content_type=target_content_type).count(),
            1,
        )

    def test_compares_complete_snapshot_content_with_published_metadata(self):
        self.publish_mapping()

        self.assertTrue(
            grc_source_content_matches(
                source_system="grc_gold",
                entity_type=GRCEntityType.COUNTRY,
                source_id=SOURCE_ID,
                target_model=Country,
                target_object_id=276,
                content_hash="a" * 64,
                is_deleted=False,
            )
        )
        self.assertFalse(
            grc_source_content_matches(
                source_system="grc_gold",
                entity_type=GRCEntityType.COUNTRY,
                source_id=SOURCE_ID,
                target_model=Country,
                target_object_id=276,
                content_hash="b" * 64,
                is_deleted=False,
            )
        )
