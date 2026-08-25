from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from django.test import SimpleTestCase
from psycopg2 import extensions

from deployments.models import OperationTypes, ProgrammeTypes, Statuses
from grc_read_model.grc_dwh_reader import GRCDWHReadError, GRCDWHSettings
from grc_read_model.grc_project_dwh_reader import load_grc_project_snapshot


COUNTRY_SOURCE_ID = UUID("10000000-0000-0000-0000-000000000001")
DISTRICT_SOURCE_ID = UUID("20000000-0000-0000-0000-000000000001")
EVENT_SOURCE_ID = UUID("30000000-0000-0000-0000-000000000001")
PROJECT_SOURCE_ID = UUID("40000000-0000-0000-0000-000000000001")
DELETED_PROJECT_SOURCE_ID = UUID("40000000-0000-0000-0000-000000000002")


def project_snapshot_rows():
    watermark = datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc)
    return {
        "SELECT CURRENT_TIMESTAMP": [{"watermark": watermark}],
        "SELECT DISTINCT sector.sectorkey": [
            {
                "sectorkey": 10,
                "goprojectprimarysectorid": 0,
                "goprojectsecondarysectortagid": 0,
                "code": "WASH",
                "name": "WASH",
            },
            {
                "sectorkey": 11,
                "goprojectprimarysectorid": 1,
                "goprojectsecondarysectortagid": 1,
                "code": "HEALTH",
                "name": "Health",
            },
        ],
        "SELECT project.grc_source_id, project.projectid": [
            {
                "grc_source_id": PROJECT_SOURCE_ID,
                "projectid": 7001,
                "goprojectid": 7001,
                "projectname": "Flood recovery",
                "reporting_ns_country_grc_source_id": COUNTRY_SOURCE_ID,
                "project_country_grc_source_id": COUNTRY_SOURCE_ID,
                "district_grc_source_ids": [DISTRICT_SOURCE_ID],
                "event_grc_source_id": EVENT_SOURCE_ID,
                "godisastertypeid": 5,
                "goprojectprimarysectorid": 0,
                "goprojectsecondarysectortagids": [1],
                "startdate": date(2026, 1, 1),
                "enddate": date(2026, 12, 31),
                "goprojectprogrammetypeid": ProgrammeTypes.BILATERAL,
                "goprojectoperationtypeid": OperationTypes.PROGRAMME,
                "goprojectstatusid": Statuses.ONGOING,
                "budgetamountchf": Decimal("250000"),
                "peopletargeted": 1000,
                "peoplereached": 750,
                "reportingcontactname": None,
                "reportingcontactrole": None,
                "reportingcontactemail": None,
                "ingestedat": watermark,
                "primarysectorbridgecount": 1,
                "unmappedsecondarysectorcount": 0,
                "unmappeddistrictcount": 0,
                "missinglocationcount": 0,
                "otheradminlevelcount": 2,
                "unknownadminlevelcount": 0,
                "duplicatelocationcount": 0,
                "primaryoperationcount": 1,
                "unmappedeventcount": 0,
                "unmappeddisastertypecount": 0,
            }
        ],
        'SELECT grc_source_id, projectid, goprojectid, ingestedat FROM "public"."factproject"': [
            {
                "grc_source_id": DELETED_PROJECT_SOURCE_ID,
                "projectid": 7002,
                "goprojectid": None,
                "ingestedat": watermark,
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


class GRCProjectDWHReaderTest(SimpleTestCase):
    def test_reads_all_project_relations_and_allows_non_adm1_locations(self):
        rows = project_snapshot_rows()
        connection = FakeConnection(rows)
        captured_kwargs = None

        def connection_factory(**kwargs):
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            return connection

        snapshot = load_grc_project_snapshot(
            GRCDWHSettings(
                host="gold.internal",
                name="gold",
                user="reader",
                password="not-logged",
            ),
            connection_factory=connection_factory,
        )

        self.assertEqual(snapshot.watermark, rows["SELECT CURRENT_TIMESTAMP"][0]["watermark"])
        self.assertEqual(len(snapshot.sectors), 2)
        self.assertEqual(snapshot.projects[0].project_key, 7001)
        self.assertEqual(snapshot.projects[0].source_id, PROJECT_SOURCE_ID)
        self.assertEqual(snapshot.projects[0].district_source_ids, (DISTRICT_SOURCE_ID,))
        self.assertEqual(snapshot.projects[0].secondary_sector_tag_ids, (1,))
        self.assertEqual(snapshot.deletions[0].project_key, 7002)
        self.assertEqual(len(connection.queries), 4)
        self.assertEqual(
            connection.session_options,
            {
                "readonly": True,
                "autocommit": False,
                "isolation_level": extensions.ISOLATION_LEVEL_REPEATABLE_READ,
            },
        )
        self.assertEqual(captured_kwargs["application_name"], "grc_go_project_sync")
        self.assertTrue(connection.closed)

    def test_rejects_incomplete_all_sector_bridge(self):
        rows = project_snapshot_rows()
        rows["SELECT project.grc_source_id, project.projectid"][0]["primarysectorbridgecount"] = 0
        connection = FakeConnection(rows)

        with self.assertRaisesRegex(GRCDWHReadError, "primary sectorkey exactly once"):
            load_grc_project_snapshot(
                GRCDWHSettings(
                    host="gold.internal",
                    name="gold",
                    user="reader",
                    password="not-logged",
                ),
                connection_factory=lambda **kwargs: connection,
            )

        self.assertTrue(connection.closed)

    def test_rejects_ambiguous_project_location_bridges(self):
        invalid_counts = {
            "unknownadminlevelcount": "without an adminlevel",
            "duplicatelocationcount": "duplicate location bridge",
        }
        for field, message in invalid_counts.items():
            rows = project_snapshot_rows()
            rows["SELECT project.grc_source_id, project.projectid"][0][field] = 1
            connection = FakeConnection(rows)

            with self.subTest(field=field), self.assertRaisesRegex(GRCDWHReadError, message):
                load_grc_project_snapshot(
                    GRCDWHSettings(
                        host="gold.internal",
                        name="gold",
                        user="reader",
                        password="not-logged",
                    ),
                    connection_factory=lambda **kwargs: connection,
                )

            self.assertTrue(connection.closed)

    def test_rejects_multiple_primary_operations(self):
        rows = project_snapshot_rows()
        rows["SELECT project.grc_source_id, project.projectid"][0]["primaryoperationcount"] = 2
        connection = FakeConnection(rows)

        with self.assertRaisesRegex(GRCDWHReadError, "at most one primary Operation"):
            load_grc_project_snapshot(
                GRCDWHSettings(
                    host="gold.internal",
                    name="gold",
                    user="reader",
                    password="not-logged",
                ),
                connection_factory=lambda **kwargs: connection,
            )

        self.assertTrue(connection.closed)
