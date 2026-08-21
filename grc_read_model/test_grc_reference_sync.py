from datetime import datetime, timezone
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from api.models import AlertLevel, Country, CountryType, DisasterType, District, Event, RegionName
from grc_read_model.grc_country_projection import GRCGoldCountry
from grc_read_model.grc_district_projection import GRCGoldDistrict
from grc_read_model.grc_event_projection import (
    GRCEventProjectionError,
    GRCGoldDisasterEvent,
    GRCGoldDisasterType,
)
from grc_read_model.grc_reference_sync import (
    GRCGoldReferenceSnapshot,
    GRCReferenceSyncError,
    publish_grc_reference_snapshot,
)
from grc_read_model.models import GRCReadModelState, GRCSourceRecord, GRCSyncRun


def country_record(**overrides):
    row = {
        "countrykey": 10,
        "gocountryid": 276,
        "goregionid": 3,
        "goregionnameid": RegionName.EUROPE,
        "gorecordtypeid": CountryType.COUNTRY,
        "name": "Germany",
        "iso2": "DE",
        "iso3": "DEU",
        "region": "Europe",
        "isactive": True,
        "independentflag": True,
        "sovereigncountrykey": None,
        "societyname": "German Red Cross",
        "centroidlatitude": Decimal("51.165691"),
        "centroidlongitude": Decimal("10.451526"),
        "bboxwest": Decimal("5.866316"),
        "bboxsouth": Decimal("47.270111"),
        "bboxeast": Decimal("15.041932"),
        "bboxnorth": Decimal("55.099161"),
        "sourceupdatedat": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "ingestedat": datetime(2026, 8, 2, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return GRCGoldCountry.from_gold_row(row)


def district_record(**overrides):
    row = {
        "locationkey": 101,
        "godistrictid": 1001,
        "gocountryid": 276,
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
    return GRCGoldDistrict.from_gold_row(row)


def disaster_type_record(**overrides):
    row = {
        "disastertypekey": 20,
        "godisastertypeid": 5,
        "code": "FL",
        "name": "Flood",
        "isactive": True,
    }
    row.update(overrides)
    return GRCGoldDisasterType.from_gold_row(row)


def event_record(**overrides):
    row = {
        "disastereventkey": 30,
        "goeventid": 3001,
        "godisastertypeid": 5,
        "name": "Central Europe floods",
        "glide": "FL-2026-000001-DEU",
        "disasterstartat": datetime(2026, 6, 1, tzinfo=timezone.utc),
        "description": "Flood response",
        "peopleaffected": 1000,
        "goifrcseveritylevelid": AlertLevel.ORANGE,
        "ifrcseveritylevelupdatedat": datetime(2026, 6, 2, tzinfo=timezone.utc),
        "gocountryids": [276],
        "godistrictids": [1001],
        "isactive": True,
        "sourceupdatedat": datetime(2026, 6, 3, tzinfo=timezone.utc),
        "ingestedat": datetime(2026, 6, 4, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return GRCGoldDisasterEvent.from_gold_row(row)


def reference_snapshot(watermark, *, country_overrides=None, event_overrides=None):
    return GRCGoldReferenceSnapshot(
        countries=[country_record(**(country_overrides or {}))],
        districts=[district_record()],
        disaster_types=[disaster_type_record()],
        events=[event_record(**(event_overrides or {}))],
        watermark=watermark,
    )


class GRCGoldReferenceSnapshotTest(SimpleTestCase):
    def test_requires_non_empty_countries_and_aware_watermark(self):
        with self.assertRaisesRegex(GRCReferenceSyncError, "complete non-empty"):
            GRCGoldReferenceSnapshot(
                countries=[],
                districts=[],
                disaster_types=[],
                events=[],
                watermark=datetime(2026, 8, 10, tzinfo=timezone.utc),
            )

        with self.assertRaisesRegex(GRCReferenceSyncError, "timezone-aware"):
            GRCGoldReferenceSnapshot(
                countries=[country_record()],
                districts=[],
                disaster_types=[],
                events=[],
                watermark=datetime(2026, 8, 10),
            )

    def test_freezes_record_sequences_and_rejects_wrong_record_types(self):
        countries = [country_record()]
        snapshot = GRCGoldReferenceSnapshot(
            countries=countries,
            districts=[],
            disaster_types=[],
            events=[],
            watermark=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
        countries.clear()

        self.assertEqual(len(snapshot.countries), 1)
        self.assertIsInstance(snapshot.countries, tuple)

        with self.assertRaisesRegex(GRCReferenceSyncError, "GRCGoldCountry"):
            GRCGoldReferenceSnapshot(
                countries=[district_record()],
                districts=[],
                disaster_types=[],
                events=[],
                watermark=datetime(2026, 8, 10, tzinfo=timezone.utc),
            )


class GRCReferenceSyncTest(TestCase):
    def setUp(self):
        DisasterType.objects.create(pk=5, name="Flood", summary="Flood")

    def test_publishes_all_dependencies_and_advances_state_atomically(self):
        watermark = datetime(2026, 8, 10, tzinfo=timezone.utc)

        sync_run = publish_grc_reference_snapshot(reference_snapshot(watermark))

        self.assertEqual(sync_run.status, GRCSyncRun.Status.SUCCEEDED)
        self.assertEqual(sync_run.source_watermark_from, None)
        self.assertEqual(sync_run.source_watermark_to, watermark)
        self.assertEqual(sync_run.rows_seen, 4)
        self.assertEqual(sync_run.rows_published, 3)
        self.assertEqual(sync_run.rows_deleted, 0)
        self.assertEqual(sync_run.details["countries"]["rows_created"], 1)
        self.assertEqual(sync_run.details["districts"]["rows_created"], 1)
        self.assertEqual(sync_run.details["disaster_types"], {"rows_seen": 1})
        self.assertEqual(sync_run.details["events"]["rows_created"], 1)
        self.assertIsNotNone(sync_run.completed_at)

        state = GRCReadModelState.objects.get(
            source_system="grc_gold",
            stream="country_district_event",
        )
        self.assertEqual(state.last_successful_watermark, watermark)
        self.assertEqual(state.last_successful_run, sync_run)
        self.assertTrue(Country.objects.filter(pk=276).exists())
        self.assertTrue(District.objects.filter(pk=1001).exists())
        self.assertTrue(Event.objects.filter(pk=3001).exists())
        self.assertEqual(GRCSourceRecord.objects.count(), 3)

    def test_rerun_is_idempotent_and_uses_previous_watermark(self):
        first_watermark = datetime(2026, 8, 10, tzinfo=timezone.utc)
        next_watermark = datetime(2026, 8, 11, tzinfo=timezone.utc)
        first_run = publish_grc_reference_snapshot(reference_snapshot(first_watermark))

        second_run = publish_grc_reference_snapshot(
            reference_snapshot(next_watermark, country_overrides={"name": "Deutschland"})
        )

        self.assertEqual(second_run.source_watermark_from, first_watermark)
        self.assertEqual(second_run.source_watermark_to, next_watermark)
        self.assertEqual(second_run.details["countries"]["rows_updated"], 1)
        self.assertEqual(Country.objects.get(pk=276).name, "Deutschland")
        self.assertEqual(Country.objects.count(), 1)
        self.assertEqual(District.objects.count(), 1)
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(GRCSourceRecord.objects.count(), 3)
        self.assertEqual(GRCSyncRun.objects.filter(status=GRCSyncRun.Status.SUCCEEDED).count(), 2)
        self.assertEqual(
            GRCSourceRecord.objects.get(target_object_id=276).last_successful_run,
            second_run,
        )
        self.assertNotEqual(first_run, second_run)

    def test_downstream_failure_rolls_back_cache_and_preserves_watermark(self):
        first_watermark = datetime(2026, 8, 10, tzinfo=timezone.utc)
        failed_watermark = datetime(2026, 8, 11, tzinfo=timezone.utc)
        successful_run = publish_grc_reference_snapshot(reference_snapshot(first_watermark))

        with self.assertRaises(GRCEventProjectionError):
            publish_grc_reference_snapshot(
                reference_snapshot(
                    failed_watermark,
                    country_overrides={"name": "Must roll back"},
                    event_overrides={"godistrictids": [999]},
                )
            )

        self.assertEqual(Country.objects.get(pk=276).name, "Germany")
        state = GRCReadModelState.objects.get(
            source_system="grc_gold",
            stream="country_district_event",
        )
        self.assertEqual(state.last_successful_watermark, first_watermark)
        self.assertEqual(state.last_successful_run, successful_run)
        self.assertEqual(
            set(GRCSourceRecord.objects.values_list("last_successful_run_id", flat=True)),
            {successful_run.pk},
        )

        failed_run = GRCSyncRun.objects.get(status=GRCSyncRun.Status.FAILED)
        self.assertEqual(failed_run.source_watermark_from, first_watermark)
        self.assertEqual(failed_run.source_watermark_to, failed_watermark)
        self.assertEqual(failed_run.details, {"error_type": "GRCEventProjectionError"})
        self.assertIn("District projection must run first", failed_run.error_message)
        self.assertIsNotNone(failed_run.completed_at)

    def test_rejects_regressive_watermark_without_touching_cache(self):
        first_watermark = datetime(2026, 8, 10, tzinfo=timezone.utc)
        publish_grc_reference_snapshot(reference_snapshot(first_watermark))

        with self.assertRaisesRegex(GRCReferenceSyncError, "must not be older"):
            publish_grc_reference_snapshot(
                reference_snapshot(
                    datetime(2026, 8, 9, tzinfo=timezone.utc),
                    country_overrides={"name": "Must not publish"},
                )
            )

        self.assertEqual(Country.objects.get(pk=276).name, "Germany")
        state = GRCReadModelState.objects.get(
            source_system="grc_gold",
            stream="country_district_event",
        )
        self.assertEqual(state.last_successful_watermark, first_watermark)
        self.assertEqual(GRCSyncRun.objects.filter(status=GRCSyncRun.Status.FAILED).count(), 1)
