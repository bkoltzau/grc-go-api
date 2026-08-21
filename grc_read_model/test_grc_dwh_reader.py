from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase
from psycopg2 import extensions

from api.models import AlertLevel, CountryType, RegionName
from grc_read_model.grc_dwh_reader import (
    GRCDWHConfigurationError,
    GRCDWHReadError,
    GRCDWHSettings,
    load_grc_reference_snapshot,
)


def dwh_environment(**overrides):
    environ = {
        "GRC_DWH_DB_HOST": "gold.internal",
        "GRC_DWH_DB_NAME": "gold",
        "GRC_DWH_DB_USER": "grc_reader",
        "GRC_DWH_DB_PASSWORD": "not-logged",
    }
    environ.update(overrides)
    return environ


def reference_rows(watermark=None):
    watermark = watermark or datetime(2026, 8, 20, tzinfo=timezone.utc)
    return {
        "SELECT CURRENT_TIMESTAMP": [{"watermark": watermark}],
        'FROM "public"."dimcountry"': [
            {
                "countrykey": 10,
                "gocountryid": 276,
                "goregionid": 3,
                "goregionnameid": RegionName.EUROPE,
                "gorecordtypeid": CountryType.COUNTRY,
                "name": "Germany",
                "iso2": "DE",
                "iso3": "DEU",
                "region": "Europe",
                "independentflag": True,
                "isactive": True,
                "societyname": "German Red Cross",
                "sovereigncountrykey": None,
                "centroidlatitude": Decimal("51.165691"),
                "centroidlongitude": Decimal("10.451526"),
                "bboxwest": Decimal("5.866316"),
                "bboxsouth": Decimal("47.270111"),
                "bboxeast": Decimal("15.041932"),
                "bboxnorth": Decimal("55.099161"),
                "sourceupdatedat": datetime(2026, 8, 19, tzinfo=timezone.utc),
                "ingestedat": watermark,
            }
        ],
        'FROM "public"."dimlocation"': [
            {
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
                "sourceupdatedat": datetime(2026, 8, 19, tzinfo=timezone.utc),
                "ingestedat": watermark,
            }
        ],
        'FROM "public"."dimdisastertype"': [
            {
                "disastertypekey": 20,
                "godisastertypeid": 5,
                "code": "FL",
                "name": "Flood",
                "isactive": True,
            }
        ],
        'FROM "public"."dimdisasterevent"': [
            {
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
                "sourceupdatedat": datetime(2026, 8, 19, tzinfo=timezone.utc),
                "ingestedat": watermark,
                "primarygocountryid": 276,
                "bridgeprimarycount": 1,
                "bridgeprimarygocountryid": 276,
                "unmappedcountrycount": 0,
                "unmappeddistrictcount": 0,
            }
        ],
    }


class FakeCursor:
    def __init__(self, rows_by_marker, queries):
        self.rows_by_marker = rows_by_marker
        self.queries = queries
        self.rows = []
        self.description = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query):
        normalized = " ".join(query.split())
        self.queries.append(normalized)
        marker = next((candidate for candidate in self.rows_by_marker if candidate in normalized), None)
        if marker is None:
            raise AssertionError(f"Unexpected query: {normalized}")
        row_dicts = self.rows_by_marker[marker]
        columns = tuple(row_dicts[0]) if row_dicts else ()
        self.description = [SimpleNamespace(name=column) for column in columns]
        self.rows = [tuple(row[column] for column in columns) for row in row_dicts]

    def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(self, rows_by_marker):
        self.rows_by_marker = rows_by_marker
        self.queries = []
        self.session_options = None
        self.closed = False

    def set_session(self, **options):
        self.session_options = options

    def cursor(self):
        return FakeCursor(self.rows_by_marker, self.queries)

    def close(self):
        self.closed = True


class GRCDWHSettingsTest(SimpleTestCase):
    def test_loads_required_values_and_safe_defaults(self):
        settings = GRCDWHSettings.from_env(dwh_environment())

        self.assertEqual(settings.host, "gold.internal")
        self.assertEqual(settings.port, 5432)
        self.assertEqual(settings.schema, "public")
        self.assertEqual(settings.sslmode, "require")
        self.assertEqual(settings.connect_timeout, 10)
        self.assertNotIn("not-logged", repr(settings))
        self.assertEqual(settings.connection_kwargs()["application_name"], "grc_go_reference_sync")

    def test_rejects_missing_or_unsafe_configuration(self):
        invalid_environments = (
            dwh_environment(GRC_DWH_DB_HOST=""),
            dwh_environment(GRC_DWH_DB_PORT="0"),
            dwh_environment(GRC_DWH_DB_PORT="65536"),
            dwh_environment(GRC_DWH_DB_SCHEMA="public; DROP SCHEMA public"),
            dwh_environment(GRC_DWH_DB_SSLMODE="unknown"),
            dwh_environment(GRC_DWH_DB_CONNECT_TIMEOUT="never"),
        )
        for environ in invalid_environments:
            with self.subTest(environ=environ), self.assertRaises(GRCDWHConfigurationError):
                GRCDWHSettings.from_env(environ)

        with self.assertRaisesRegex(GRCDWHConfigurationError, "schema"):
            GRCDWHSettings(
                host="gold.internal",
                name="gold",
                user="grc_reader",
                password="not-logged",
                schema="public; DROP SCHEMA public",
            )


class GRCDWHReaderTest(SimpleTestCase):
    def test_reads_typed_records_in_one_read_only_repeatable_snapshot(self):
        rows = reference_rows()
        connection = FakeConnection(rows)
        captured_kwargs = None

        def connection_factory(**kwargs):
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            return connection

        snapshot = load_grc_reference_snapshot(
            GRCDWHSettings.from_env(dwh_environment()),
            connection_factory=connection_factory,
        )

        self.assertEqual(snapshot.watermark, rows["SELECT CURRENT_TIMESTAMP"][0]["watermark"])
        self.assertEqual(snapshot.countries[0].go_country_id, 276)
        self.assertEqual(snapshot.districts[0].go_district_id, 1001)
        self.assertEqual(snapshot.disaster_types[0].go_disaster_type_id, 5)
        self.assertEqual(snapshot.events[0].go_event_id, 3001)
        self.assertEqual(snapshot.events[0].go_country_ids, (276,))
        self.assertEqual(snapshot.events[0].go_district_ids, (1001,))
        self.assertEqual(len(connection.queries), 5)
        self.assertIn('FROM "public"."dimcountry"', connection.queries[1])
        self.assertIn('FROM "public"."dimlocation"', connection.queries[2])
        self.assertIn('FROM "public"."dimdisastertype"', connection.queries[3])
        self.assertIn('FROM "public"."dimdisasterevent"', connection.queries[4])
        self.assertEqual(
            connection.session_options,
            {
                "readonly": True,
                "autocommit": False,
                "isolation_level": extensions.ISOLATION_LEVEL_REPEATABLE_READ,
            },
        )
        self.assertEqual(captured_kwargs["dbname"], "gold")
        self.assertTrue(connection.closed)

    def test_rejects_incomplete_event_geography_and_closes_connection(self):
        rows = reference_rows()
        rows['FROM "public"."dimdisasterevent"'][0]["unmappedcountrycount"] = 1
        connection = FakeConnection(rows)

        with self.assertRaisesRegex(GRCDWHReadError, "without a mapped gocountryid"):
            load_grc_reference_snapshot(
                GRCDWHSettings.from_env(dwh_environment()),
                connection_factory=lambda **kwargs: connection,
            )

        self.assertTrue(connection.closed)
