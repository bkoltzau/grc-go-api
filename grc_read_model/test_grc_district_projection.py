from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from django.contrib.gis.geos import Polygon
from django.test import SimpleTestCase, TestCase

from api.models import Country, District
from grc_read_model.grc_district_projection import (
    GRCDistrictProjectionError,
    GRCGoldDistrict,
    publish_grc_district_snapshot,
)
from grc_read_model.grc_identity import publish_grc_source_record
from grc_read_model.models import GRCEntityType, GRCSourceRecord, GRCSyncRun


COUNTRY_SOURCE_ID = UUID("10000000-0000-0000-0000-000000000001")
OTHER_COUNTRY_SOURCE_ID = UUID("10000000-0000-0000-0000-000000000002")
DISTRICT_SOURCE_ID = UUID("20000000-0000-0000-0000-000000000001")
OTHER_DISTRICT_SOURCE_ID = UUID("20000000-0000-0000-0000-000000000002")


def district_row(**overrides):
    row = {
        "grc_source_id": DISTRICT_SOURCE_ID,
        "locationkey": 101,
        "godistrictid": 1001,
        "country_grc_source_id": COUNTRY_SOURCE_ID,
        "countrykey": 10,
        "adminlevel": 1,
        "pcode": "DE-BY",
        "name": "Bavaria",
        "latitude": Decimal("48.946756"),
        "longitude": Decimal("11.403871"),
        "isactive": True,
        "sourceupdatedat": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "ingestedat": datetime(2026, 8, 2, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


class GRCGoldDistrictTest(SimpleTestCase):
    def test_maps_the_approved_adm1_contract(self):
        record = GRCGoldDistrict.from_gold_row(district_row())

        self.assertEqual(record.go_district_id, 1001)
        self.assertEqual(record.country_source_id, COUNTRY_SOURCE_ID)
        self.assertEqual(record.admin_level, 1)
        self.assertEqual(len(record.content_hash()), 64)

        defaults = record.district_defaults(276)
        self.assertEqual(defaults["country_id"], 276)
        self.assertEqual(defaults["code"], "DE-BY")
        self.assertEqual(defaults["centroid"].srid, 4326)

    def test_content_hash_ignores_dwh_surrogate_and_ingestion_metadata(self):
        first = GRCGoldDistrict.from_gold_row(district_row())
        rekeyed = GRCGoldDistrict.from_gold_row(
            district_row(
                locationkey=999,
                godistrictid=None,
                countrykey=9999,
                sourceupdatedat=datetime(2026, 8, 3, tzinfo=timezone.utc),
                ingestedat=datetime(2026, 8, 4, tzinfo=timezone.utc),
            )
        )

        self.assertEqual(first.content_hash(), rekeyed.content_hash())

    def test_rejects_non_adm1_and_invalid_required_values(self):
        invalid_rows = (
            district_row(adminlevel=0),
            district_row(adminlevel=2),
            district_row(pcode=None),
            district_row(pcode="TOO-LONG-CODE"),
            district_row(name="x" * 101),
            district_row(latitude=Decimal("91")),
            district_row(ingestedat=datetime(2026, 8, 2)),
        )

        for row in invalid_rows:
            with self.subTest(row=row), self.assertRaises(GRCDistrictProjectionError):
                GRCGoldDistrict.from_gold_row(row)


class GRCDistrictPublisherTest(TestCase):
    def setUp(self):
        self.country = Country.objects.create(
            pk=276,
            name="Germany",
            iso="DE",
            iso3="DEU",
        )
        self.sync_run = GRCSyncRun.objects.create(pipeline="dimlocation-adm1")
        publish_grc_source_record(
            source_system="grc_gold",
            entity_type=GRCEntityType.COUNTRY,
            source_id=COUNTRY_SOURCE_ID,
            target_model=Country,
            target_object_id=self.country.pk,
            source_ingested_at=None,
            content_hash="a" * 64,
            is_deleted=False,
            sync_run=self.sync_run,
        )

    def test_publishes_into_existing_district_and_metadata(self):
        record = GRCGoldDistrict.from_gold_row(district_row())

        result = publish_grc_district_snapshot([record], self.sync_run)

        self.assertEqual(result.rows_seen, 1)
        self.assertEqual(result.rows_created, 1)
        self.assertEqual(result.rows_updated, 0)

        district = District.objects.get(pk=1001)
        self.assertEqual(district.name, "Bavaria")
        self.assertEqual(district.code, "DE-BY")
        self.assertEqual(district.country, self.country)
        self.assertFalse(district.is_deprecated)
        self.assertAlmostEqual(district.centroid.x, 11.403871)

        source_record = GRCSourceRecord.objects.get(
            source_system="grc_gold",
            entity_type=GRCEntityType.DISTRICT,
            source_id=str(DISTRICT_SOURCE_ID),
        )
        self.assertEqual(source_record.target_object, district)
        self.assertEqual(source_record.source_ingested_at, record.ingested_at)
        self.assertEqual(source_record.content_hash, record.content_hash())

    def test_rerun_preserves_fields_not_owned_by_gold(self):
        original_bbox = Polygon.from_bbox((5.0, 47.0, 15.0, 55.0))
        original_bbox.srid = 4326
        District.objects.create(
            pk=1001,
            name="Old name",
            code="OLD",
            country=self.country,
            is_enclave=True,
            bbox=original_bbox,
        )
        record = GRCGoldDistrict.from_gold_row(district_row())

        first_result = publish_grc_district_snapshot([record], self.sync_run)
        second_result = publish_grc_district_snapshot([record], self.sync_run)

        self.assertEqual(first_result.rows_created, 0)
        self.assertEqual(second_result.rows_updated, 1)
        self.assertEqual(second_result.rows_unchanged, 1)
        self.assertEqual(District.objects.count(), 1)
        district = District.objects.get(pk=1001)
        self.assertEqual(district.name, "Bavaria")
        self.assertTrue(district.is_enclave)
        self.assertEqual(district.bbox, original_bbox)
        self.assertEqual(GRCSourceRecord.objects.count(), 2)

    def test_maps_inactive_to_deprecated_not_deleted(self):
        record = GRCGoldDistrict.from_gold_row(district_row(isactive=False))

        result = publish_grc_district_snapshot([record], self.sync_run)

        self.assertEqual(result.rows_deprecated, 1)
        self.assertTrue(District.objects.get(pk=1001).is_deprecated)
        self.assertFalse(
            GRCSourceRecord.objects.get(source_id=str(DISTRICT_SOURCE_ID)).is_deleted
        )

    def test_requires_country_projection_to_exist_first(self):
        self.country.delete()
        record = GRCGoldDistrict.from_gold_row(district_row())

        with self.assertRaises(GRCDistrictProjectionError):
            publish_grc_district_snapshot([record], self.sync_run)

        self.assertFalse(District.objects.exists())
        self.assertFalse(
            GRCSourceRecord.objects.filter(entity_type=GRCEntityType.DISTRICT).exists()
        )

    def test_rejects_duplicate_source_or_go_ids_before_writing(self):
        first = GRCGoldDistrict.from_gold_row(district_row())
        duplicate_source = GRCGoldDistrict.from_gold_row(
            district_row(godistrictid=1002)
        )
        duplicate_target = GRCGoldDistrict.from_gold_row(
            district_row(locationkey=102, grc_source_id=OTHER_DISTRICT_SOURCE_ID)
        )

        for records in ([first, duplicate_source], [first, duplicate_target]):
            with self.subTest(records=records), self.assertRaises(GRCDistrictProjectionError):
                publish_grc_district_snapshot(records, self.sync_run)

        self.assertFalse(District.objects.exists())
        self.assertFalse(
            GRCSourceRecord.objects.filter(entity_type=GRCEntityType.DISTRICT).exists()
        )

    def test_rejects_conflicting_country_mapping_before_writing(self):
        first = GRCGoldDistrict.from_gold_row(district_row())
        conflicting_country = GRCGoldDistrict.from_gold_row(
            district_row(
                locationkey=102,
                grc_source_id=OTHER_DISTRICT_SOURCE_ID,
                godistrictid=1002,
                country_grc_source_id=OTHER_COUNTRY_SOURCE_ID,
                pcode="DE-BE",
                name="Berlin",
            )
        )

        with self.assertRaisesRegex(GRCDistrictProjectionError, "conflicting grc_source_id"):
            publish_grc_district_snapshot([first, conflicting_country], self.sync_run)

        self.assertFalse(District.objects.exists())
        self.assertEqual(
            GRCSourceRecord.objects.filter(entity_type=GRCEntityType.COUNTRY).count(),
            1,
        )

    def test_requires_saved_running_sync_run(self):
        record = GRCGoldDistrict.from_gold_row(district_row())
        finished_run = GRCSyncRun.objects.create(
            pipeline="finished-dimlocation-adm1",
            status=GRCSyncRun.Status.SUCCEEDED,
        )

        invalid_runs = (GRCSyncRun(pipeline="unsaved"), finished_run)
        for sync_run in invalid_runs:
            with self.subTest(sync_run=sync_run), self.assertRaises(GRCDistrictProjectionError):
                publish_grc_district_snapshot([record], sync_run)

        self.assertFalse(District.objects.exists())
        self.assertFalse(
            GRCSourceRecord.objects.filter(entity_type=GRCEntityType.DISTRICT).exists()
        )
