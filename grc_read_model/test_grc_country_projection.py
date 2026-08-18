from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal

from django.db import IntegrityError
from django.test import SimpleTestCase, TestCase

from api.models import Country, CountryType, Region, RegionName
from grc_read_model.grc_country_projection import (
    GRCCountryProjectionError,
    GRCGoldCountry,
    publish_grc_country_snapshot,
)
from grc_read_model.models import GRCEntityType, GRCSourceRecord, GRCSyncRun


def country_row(**overrides):
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
    return row


class GRCGoldCountryTest(SimpleTestCase):
    def test_maps_the_approved_gold_contract(self):
        record = GRCGoldCountry.from_gold_row(country_row())

        self.assertEqual(record.go_country_id, 276)
        self.assertEqual(record.go_record_type_id, CountryType.COUNTRY)
        self.assertEqual(record.go_region_name_id, RegionName.EUROPE)
        self.assertEqual(record.region_label, "Europe")
        self.assertEqual(len(record.content_hash()), 64)

        defaults = record.country_defaults()
        self.assertEqual(defaults["record_type"], CountryType.COUNTRY)
        self.assertEqual(defaults["region_id"], 3)
        self.assertEqual(defaults["centroid"].srid, 4326)
        self.assertEqual(defaults["bbox"].srid, 4326)

    def test_content_hash_ignores_ingestion_metadata(self):
        first = GRCGoldCountry.from_gold_row(country_row())
        changed_ingestion = GRCGoldCountry.from_gold_row(
            country_row(
                sourceupdatedat=datetime(2026, 8, 3, tzinfo=timezone.utc),
                ingestedat=datetime(2026, 8, 4, tzinfo=timezone.utc),
            )
        )

        self.assertEqual(first.content_hash(), changed_ingestion.content_hash())

    def test_rejects_missing_or_semantically_invalid_values(self):
        invalid_rows = (
            country_row(name=""),
            country_row(iso2="de"),
            country_row(gorecordtypeid=999),
            country_row(goregionnameid=999),
            country_row(bboxsouth=Decimal("60"), bboxnorth=Decimal("50")),
            country_row(ingestedat=datetime(2026, 8, 2)),
        )

        for row in invalid_rows:
            with self.subTest(row=row), self.assertRaises(GRCCountryProjectionError):
                GRCGoldCountry.from_gold_row(row)


class GRCCountryPublisherTest(TestCase):
    def setUp(self):
        self.sync_run = GRCSyncRun.objects.create(pipeline="dimcountry")

    def test_publishes_into_existing_go_models_and_metadata(self):
        germany = GRCGoldCountry.from_gold_row(country_row())

        result = publish_grc_country_snapshot([germany], self.sync_run)

        self.assertEqual(result.rows_seen, 1)
        self.assertEqual(result.rows_created, 1)
        self.assertEqual(result.rows_updated, 0)

        region = Region.objects.get(pk=3)
        self.assertEqual(region.name, RegionName.EUROPE)
        self.assertEqual(region.label, "Europe")

        country = Country.objects.get(pk=276)
        self.assertEqual(country.name, "Germany")
        self.assertEqual(country.iso, "DE")
        self.assertEqual(country.iso3, "DEU")
        self.assertEqual(country.society_name, "German Red Cross")
        self.assertEqual(country.region_id, 3)
        self.assertFalse(country.is_deprecated)
        self.assertAlmostEqual(country.centroid.x, 10.451526)

        source_record = GRCSourceRecord.objects.get(
            source_system="grc_gold",
            entity_type=GRCEntityType.COUNTRY,
            source_id="276",
        )
        self.assertEqual(source_record.target_object, country)
        self.assertEqual(source_record.source_ingested_at, germany.ingested_at)
        self.assertEqual(source_record.content_hash, germany.content_hash())
        self.assertEqual(source_record.last_successful_run, self.sync_run)

    def test_rerun_is_idempotent_and_preserves_fields_not_owned_by_gold(self):
        Country.objects.create(
            pk=276,
            name="Old name",
            iso="DE",
            iso3="DEU",
            society_url="https://example.invalid/national-society",
        )
        record = GRCGoldCountry.from_gold_row(country_row())

        first_result = publish_grc_country_snapshot([record], self.sync_run)
        second_result = publish_grc_country_snapshot([record], self.sync_run)

        self.assertEqual(first_result.rows_created, 0)
        self.assertEqual(second_result.rows_created, 0)
        self.assertEqual(second_result.rows_updated, 1)
        self.assertEqual(Country.objects.count(), 1)
        country = Country.objects.get(pk=276)
        self.assertEqual(country.name, "Germany")
        self.assertEqual(country.society_url, "https://example.invalid/national-society")
        self.assertEqual(GRCSourceRecord.objects.count(), 1)

    def test_resolves_sovereign_country_in_a_second_pass(self):
        sovereign = GRCGoldCountry.from_gold_row(country_row())
        territory_row = deepcopy(country_row())
        territory_row.update(
            {
                "countrykey": 11,
                "gocountryid": 999,
                "name": "Example Territory",
                "iso2": "XT",
                "iso3": "XTR",
                "independentflag": False,
                "sovereigncountrykey": 10,
            }
        )
        territory = GRCGoldCountry.from_gold_row(territory_row)

        publish_grc_country_snapshot([territory, sovereign], self.sync_run)

        self.assertEqual(Country.objects.get(pk=999).sovereign_state_id, 276)

    def test_maps_inactive_to_deprecated_without_marking_source_deleted(self):
        inactive = GRCGoldCountry.from_gold_row(country_row(isactive=False))

        result = publish_grc_country_snapshot([inactive], self.sync_run)

        self.assertEqual(result.rows_deprecated, 1)
        self.assertTrue(Country.objects.get(pk=276).is_deprecated)
        self.assertFalse(GRCSourceRecord.objects.get(source_id="276").is_deleted)

    def test_rejects_an_incomplete_or_ambiguous_snapshot_before_writing(self):
        valid = GRCGoldCountry.from_gold_row(country_row())
        unresolved = GRCGoldCountry.from_gold_row(
            country_row(sovereigncountrykey=999)
        )
        duplicate = GRCGoldCountry.from_gold_row(
            country_row(countrykey=11)
        )

        for records in ([unresolved], [valid, duplicate]):
            with self.subTest(records=records), self.assertRaises(GRCCountryProjectionError):
                publish_grc_country_snapshot(records, self.sync_run)

        self.assertFalse(Country.objects.exists())
        self.assertFalse(Region.objects.exists())
        self.assertFalse(GRCSourceRecord.objects.exists())

    def test_database_failure_rolls_back_the_whole_publication(self):
        Country.objects.create(
            pk=1,
            name="Existing country",
            iso="DE",
            iso3="DEU",
        )
        record = GRCGoldCountry.from_gold_row(country_row())

        with self.assertRaises(IntegrityError):
            publish_grc_country_snapshot([record], self.sync_run)

        self.assertEqual(Country.objects.count(), 1)
        self.assertFalse(Region.objects.exists())
        self.assertFalse(GRCSourceRecord.objects.exists())

    def test_requires_a_running_sync_run(self):
        self.sync_run.status = GRCSyncRun.Status.SUCCEEDED
        self.sync_run.save(update_fields=("status",))
        record = GRCGoldCountry.from_gold_row(country_row())

        with self.assertRaises(GRCCountryProjectionError):
            publish_grc_country_snapshot([record], self.sync_run)

        self.assertFalse(Country.objects.exists())
