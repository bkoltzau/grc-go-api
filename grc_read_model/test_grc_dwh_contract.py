from types import SimpleNamespace

from django.test import SimpleTestCase
from psycopg2 import extensions

from grc_read_model.grc_dwh_contract import (
    GRC_GOLD_CONTRACT_REQUIREMENTS,
    GRCDWHContractError,
    check_grc_gold_contract,
)
from grc_read_model.grc_dwh_reader import GRCDWHSettings


def compatible_schema_rows(scopes=("reference", "project")):
    rows = {}
    for requirement in GRC_GOLD_CONTRACT_REQUIREMENTS:
        if requirement.scope not in scopes:
            continue
        key = (requirement.table, requirement.column)
        existing = rows.get(key)
        row = {
            "table_name": requirement.table,
            "column_name": requirement.column,
            "data_type": requirement.data_types[0],
            "character_maximum_length": requirement.minimum_character_length,
        }
        if existing is not None:
            if existing != row:
                raise AssertionError(f"conflicting test fixture requirement for {key}")
            continue
        rows[key] = row
    return list(rows.values())


class FakeCursor:
    def __init__(self, rows, calls):
        self.rows = rows
        self.calls = calls
        self.description = [
            SimpleNamespace(name="table_name"),
            SimpleNamespace(name="column_name"),
            SimpleNamespace(name="data_type"),
            SimpleNamespace(name="character_maximum_length"),
        ]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, parameters):
        self.calls.append((" ".join(query.split()), parameters))

    def fetchall(self):
        fields = ("table_name", "column_name", "data_type", "character_maximum_length")
        return [tuple(row[field] for field in fields) for row in self.rows]


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.session_options = None
        self.closed = False

    def set_session(self, **options):
        self.session_options = options

    def cursor(self):
        return FakeCursor(self.rows, self.calls)

    def close(self):
        self.closed = True


def dwh_settings():
    return GRCDWHSettings(
        host="gold.internal",
        name="gold",
        user="reader",
        password="not-logged",
    )


class GRCDWHContractTest(SimpleTestCase):
    def test_accepts_complete_reference_and_project_schema_read_only(self):
        connection = FakeConnection(compatible_schema_rows())
        captured_kwargs = None

        def connection_factory(**kwargs):
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            return connection

        report = check_grc_gold_contract(
            dwh_settings(),
            connection_factory=connection_factory,
        )

        self.assertTrue(report.is_compatible)
        self.assertEqual(report.scopes, ("reference", "project"))
        self.assertEqual(connection.calls[0][1], ("public",))
        self.assertEqual(
            connection.session_options,
            {
                "readonly": True,
                "autocommit": False,
                "isolation_level": extensions.ISOLATION_LEVEL_REPEATABLE_READ,
            },
        )
        self.assertEqual(captured_kwargs["application_name"], "grc_go_contract_check")
        self.assertTrue(connection.closed)

    def test_reports_missing_table_column_and_incompatible_types(self):
        rows = compatible_schema_rows()
        rows = [row for row in rows if row["table_name"] != "bridgedisastereventlocation"]
        rows = [
            row
            for row in rows
            if not (row["table_name"] == "dimcountry" and row["column_name"] == "goregionid")
        ]
        for row in rows:
            if row["table_name"] == "factproject" and row["column_name"] == "ingestedat":
                row["data_type"] = "timestamp without time zone"
            if row["table_name"] == "factproject" and row["column_name"] == "projectname":
                row["character_maximum_length"] = 100
        connection = FakeConnection(rows)

        report = check_grc_gold_contract(
            dwh_settings(),
            connection_factory=lambda **kwargs: connection,
        )

        self.assertFalse(report.is_compatible)
        self.assertEqual(report.missing_tables, ("bridgedisastereventlocation",))
        self.assertIn("dimcountry.goregionid", report.missing_columns)
        mismatch_ids = {mismatch.identity for mismatch in report.incompatible_columns}
        self.assertEqual(
            mismatch_ids,
            {"factproject.ingestedat", "factproject.projectname"},
        )
        with self.assertRaisesRegex(GRCDWHContractError, "timestamp without time zone"):
            report.raise_for_errors()

    def test_reference_scope_does_not_require_project_only_columns(self):
        connection = FakeConnection(compatible_schema_rows(("reference",)))

        report = check_grc_gold_contract(
            dwh_settings(),
            scopes=("reference",),
            connection_factory=lambda **kwargs: connection,
        )

        self.assertTrue(report.is_compatible)
        self.assertEqual(report.scopes, ("reference",))

    def test_rejects_unknown_or_malformed_scope_without_connecting(self):
        for scopes in (("unknown",), "reference", (1,)):
            with self.subTest(scopes=scopes), self.assertRaises(GRCDWHContractError):
                check_grc_gold_contract(
                    dwh_settings(),
                    scopes=scopes,
                    connection_factory=lambda **kwargs: self.fail("must not connect"),
                )
