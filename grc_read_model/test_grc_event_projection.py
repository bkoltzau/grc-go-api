from datetime import datetime, timezone

from django.test import SimpleTestCase, TestCase

from api.models import AlertLevel, Country, DisasterType, District, Event, Region, RegionName, VisibilityChoices
from grc_read_model.grc_event_projection import (
    GRCEventProjectionError,
    GRCGoldDisasterEvent,
    GRCGoldDisasterType,
    publish_grc_event_snapshot,
    validate_grc_disaster_types,
)
from grc_read_model.models import GRCEntityType, GRCSourceRecord, GRCSyncRun


def disaster_type_row(**overrides):
    row = {
        "disastertypekey": 20,
        "godisastertypeid": 5,
        "code": "FL",
        "name": "Flood",
        "isactive": True,
    }
    row.update(overrides)
    return row


def event_row(**overrides):
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
    return row


class GRCGoldDisasterTypeTest(SimpleTestCase):
    def test_maps_explicit_go_identity(self):
        disaster_type = GRCGoldDisasterType.from_gold_row(disaster_type_row())

        self.assertEqual(disaster_type.go_disaster_type_id, 5)
        self.assertEqual(disaster_type.code, "FL")

    def test_rejects_invalid_contract(self):
        invalid_rows = (
            disaster_type_row(godisastertypeid=0),
            disaster_type_row(code=""),
            disaster_type_row(name="x" * 201),
            disaster_type_row(isactive=None),
        )
        for row in invalid_rows:
            with self.subTest(row=row), self.assertRaises(GRCEventProjectionError):
                GRCGoldDisasterType.from_gold_row(row)


class GRCGoldDisasterEventTest(SimpleTestCase):
    def test_maps_the_approved_event_contract(self):
        event = GRCGoldDisasterEvent.from_gold_row(event_row())

        self.assertEqual(event.go_event_id, 3001)
        self.assertEqual(event.go_country_ids, (276,))
        self.assertEqual(event.go_district_ids, (1001,))
        self.assertEqual(event.event_defaults()["visibility"], VisibilityChoices.MEMBERSHIP)
        self.assertEqual(len(event.content_hash()), 64)

    def test_content_hash_ignores_dwh_surrogate_and_ingestion_metadata(self):
        first = GRCGoldDisasterEvent.from_gold_row(event_row())
        rekeyed = GRCGoldDisasterEvent.from_gold_row(
            event_row(
                disastereventkey=999,
                sourceupdatedat=datetime(2026, 6, 5, tzinfo=timezone.utc),
                ingestedat=datetime(2026, 6, 6, tzinfo=timezone.utc),
            )
        )

        self.assertEqual(first.content_hash(), rekeyed.content_hash())

    def test_rejects_invalid_event_values(self):
        invalid_rows = (
            event_row(goeventid=0),
            event_row(name=""),
            event_row(glide="x" * 19),
            event_row(disasterstartat=datetime(2026, 6, 1)),
            event_row(peopleaffected=-1),
            event_row(goifrcseveritylevelid=99),
            event_row(gocountryids=[]),
            event_row(gocountryids=[276, 276]),
            event_row(godistrictids=[1001, 1001]),
            event_row(ingestedat=datetime(2026, 6, 4)),
        )
        for row in invalid_rows:
            with self.subTest(row=row), self.assertRaises(GRCEventProjectionError):
                GRCGoldDisasterEvent.from_gold_row(row)


class GRCEventPublisherTest(TestCase):
    def setUp(self):
        self.region = Region.objects.create(pk=10, name=RegionName.EUROPE, label="Europe")
        self.country = Country.objects.create(
            pk=276,
            name="Germany",
            iso="DE",
            iso3="DEU",
            region=self.region,
        )
        self.other_country = Country.objects.create(
            pk=250,
            name="France",
            iso="FR",
            iso3="FRA",
            region=self.region,
        )
        self.district = District.objects.create(
            pk=1001,
            name="Bavaria",
            code="DE-BY",
            country=self.country,
        )
        self.other_district = District.objects.create(
            pk=1002,
            name="Grand Est",
            code="FR-GES",
            country=self.other_country,
        )
        self.disaster_type = DisasterType.objects.create(pk=5, name="Flood", summary="Flood")
        self.sync_run = GRCSyncRun.objects.create(pipeline="dimdisasterevent")

    def test_validates_existing_disaster_type_identity(self):
        validate_grc_disaster_types(
            [GRCGoldDisasterType.from_gold_row(disaster_type_row())],
            required_go_ids=[5],
        )

        with self.assertRaises(GRCEventProjectionError):
            validate_grc_disaster_types(
                [GRCGoldDisasterType.from_gold_row(disaster_type_row(godisastertypeid=99))]
            )

    def test_allows_unreferenced_inactive_type_but_rejects_it_when_required(self):
        inactive_type = GRCGoldDisasterType.from_gold_row(disaster_type_row(isactive=False))

        validate_grc_disaster_types([inactive_type])
        with self.assertRaisesRegex(GRCEventProjectionError, "inactive"):
            validate_grc_disaster_types([inactive_type], required_go_ids=[5])

    def test_publishes_event_and_complete_geography(self):
        record = GRCGoldDisasterEvent.from_gold_row(
            event_row(
                gocountryids=[276, 250],
                godistrictids=[1001, 1002],
            )
        )

        result = publish_grc_event_snapshot([record], self.sync_run)

        self.assertEqual(result.rows_created, 1)
        event = Event.objects.get(pk=3001)
        self.assertEqual(event.name, "Central Europe floods")
        self.assertEqual(event.dtype, self.disaster_type)
        self.assertEqual(event.visibility, VisibilityChoices.MEMBERSHIP)
        self.assertEqual(event.num_affected, 1000)
        self.assertSetEqual(set(event.countries.values_list("id", flat=True)), {276, 250})
        self.assertSetEqual(set(event.countries_for_preview.values_list("id", flat=True)), {276, 250})
        self.assertSetEqual(set(event.districts.values_list("id", flat=True)), {1001, 1002})
        self.assertSetEqual(set(event.regions.values_list("id", flat=True)), {10})

        source_record = GRCSourceRecord.objects.get(
            source_system="grc_gold",
            entity_type=GRCEntityType.DISASTER_EVENT,
            source_id="3001",
        )
        self.assertEqual(source_record.target_object, event)
        self.assertEqual(source_record.content_hash, record.content_hash())

    def test_rerun_is_idempotent_and_preserves_unowned_fields(self):
        Event.objects.create(
            pk=3001,
            name="Old name",
            dtype=self.disaster_type,
            disaster_start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
            slug="old-event",
            hide_field_report_map=True,
        )
        record = GRCGoldDisasterEvent.from_gold_row(event_row())

        first_result = publish_grc_event_snapshot([record], self.sync_run)
        second_result = publish_grc_event_snapshot([record], self.sync_run)

        self.assertEqual(first_result.rows_created, 0)
        self.assertEqual(second_result.rows_updated, 1)
        self.assertEqual(Event.objects.count(), 1)
        event = Event.objects.get(pk=3001)
        self.assertEqual(event.name, "Central Europe floods")
        self.assertEqual(event.slug, "old-event")
        self.assertTrue(event.hide_field_report_map)
        self.assertEqual(GRCSourceRecord.objects.count(), 1)

    def test_rejects_missing_or_inconsistent_geography_before_writing(self):
        missing_country = GRCGoldDisasterEvent.from_gold_row(event_row(gocountryids=[999]))
        outside_country = GRCGoldDisasterEvent.from_gold_row(event_row(godistrictids=[1002]))

        for record in (missing_country, outside_country):
            with self.subTest(record=record), self.assertRaises(GRCEventProjectionError):
                publish_grc_event_snapshot([record], self.sync_run)

        self.assertFalse(Event.objects.exists())
        self.assertFalse(GRCSourceRecord.objects.exists())

    def test_rejects_inactive_or_duplicate_events_before_writing(self):
        first = GRCGoldDisasterEvent.from_gold_row(event_row())
        inactive = GRCGoldDisasterEvent.from_gold_row(event_row(isactive=False))
        duplicate_source = GRCGoldDisasterEvent.from_gold_row(event_row(goeventid=3002))
        duplicate_target = GRCGoldDisasterEvent.from_gold_row(event_row(disastereventkey=31))

        invalid_batches = ([inactive], [first, duplicate_source], [first, duplicate_target])
        for records in invalid_batches:
            with self.subTest(records=records), self.assertRaises(GRCEventProjectionError):
                publish_grc_event_snapshot(records, self.sync_run)

        self.assertFalse(Event.objects.exists())
        self.assertFalse(GRCSourceRecord.objects.exists())

    def test_requires_saved_running_sync_run(self):
        record = GRCGoldDisasterEvent.from_gold_row(event_row())
        finished_run = GRCSyncRun.objects.create(
            pipeline="finished-dimdisasterevent",
            status=GRCSyncRun.Status.SUCCEEDED,
        )

        invalid_runs = (GRCSyncRun(pipeline="unsaved"), finished_run)
        for sync_run in invalid_runs:
            with self.subTest(sync_run=sync_run), self.assertRaises(GRCEventProjectionError):
                publish_grc_event_snapshot([record], sync_run)

        self.assertFalse(Event.objects.exists())
        self.assertFalse(GRCSourceRecord.objects.exists())
