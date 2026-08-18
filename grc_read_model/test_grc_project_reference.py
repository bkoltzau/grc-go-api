from datetime import date, datetime

from django.test import SimpleTestCase, TestCase

from deployments.models import OperationTypes, ProgrammeTypes, Sector, SectorTag, Statuses
from grc_read_model.grc_project_reference import (
    GRCGoldProjectSector,
    GRCProjectControlledValues,
    GRCProjectReferenceError,
    validate_grc_project_sectors,
)


def controlled_values_row(**overrides):
    row = {
        "startdate": date(2026, 1, 1),
        "enddate": date(2026, 12, 31),
        "goprojectprogrammetypeid": ProgrammeTypes.BILATERAL,
        "goprojectoperationtypeid": OperationTypes.PROGRAMME,
        "goprojectstatusid": Statuses.ONGOING,
    }
    row.update(overrides)
    return row


def sector_row(**overrides):
    row = {
        "sectorkey": 10,
        "goprojectprimarysectorid": 0,
        "goprojectsecondarysectortagid": 0,
        "code": "WASH",
        "name": "WASH",
    }
    row.update(overrides)
    return row


class GRCProjectControlledValuesTest(SimpleTestCase):
    def test_accepts_exact_upstream_go_values_including_zero(self):
        values = GRCProjectControlledValues.from_gold_row(controlled_values_row())

        self.assertEqual(values.programme_type, ProgrammeTypes.BILATERAL)
        self.assertEqual(values.operation_type, OperationTypes.PROGRAMME)
        self.assertEqual(values.status, Statuses.ONGOING)
        self.assertEqual(
            values.project_defaults(as_of=date(2026, 6, 1)),
            {
                "start_date": date(2026, 1, 1),
                "end_date": date(2026, 12, 31),
                "programme_type": ProgrammeTypes.BILATERAL,
                "operation_type": OperationTypes.PROGRAMME,
                "status": Statuses.ONGOING,
            },
        )

    def test_uses_upstream_date_boundaries_for_status(self):
        values = GRCProjectControlledValues.from_gold_row(controlled_values_row())

        self.assertEqual(values.derived_status(date(2025, 12, 31)), Statuses.PLANNED)
        self.assertEqual(values.derived_status(date(2026, 1, 1)), Statuses.ONGOING)
        self.assertEqual(values.derived_status(date(2026, 12, 31)), Statuses.ONGOING)
        self.assertEqual(values.derived_status(date(2027, 1, 1)), Statuses.COMPLETED)

    def test_rejects_semantically_incompatible_or_malformed_values(self):
        invalid_rows = (
            controlled_values_row(goprojectprogrammetypeid=99),
            controlled_values_row(goprojectoperationtypeid=99),
            controlled_values_row(goprojectstatusid=99),
            controlled_values_row(goprojectstatusid=True),
            controlled_values_row(startdate=datetime(2026, 1, 1)),
            controlled_values_row(enddate=date(2025, 12, 31)),
        )

        for row in invalid_rows:
            with self.subTest(row=row), self.assertRaises(GRCProjectReferenceError):
                GRCProjectControlledValues.from_gold_row(row)

    def test_rejects_gold_status_that_conflicts_with_upstream_derivation(self):
        values = GRCProjectControlledValues.from_gold_row(
            controlled_values_row(goprojectstatusid=Statuses.PLANNED)
        )

        with self.assertRaisesRegex(GRCProjectReferenceError, "conflicts"):
            values.project_defaults(as_of=date(2026, 6, 1))


class GRCGoldProjectSectorTest(SimpleTestCase):
    def test_maps_distinct_upstream_primary_and_secondary_ids(self):
        sector = GRCGoldProjectSector.from_gold_row(sector_row())

        self.assertEqual(sector.primary_sector_id, 0)
        self.assertEqual(sector.secondary_sector_tag_id, 0)

    def test_allows_sector_without_secondary_mapping(self):
        sector = GRCGoldProjectSector.from_gold_row(
            sector_row(goprojectsecondarysectortagid=None)
        )

        self.assertIsNone(sector.secondary_sector_tag_id)

    def test_rejects_invalid_sector_contract(self):
        invalid_rows = (
            sector_row(sectorkey=0),
            sector_row(goprojectprimarysectorid=-1),
            sector_row(goprojectsecondarysectortagid=-1),
            sector_row(code=""),
            sector_row(name="x" * 201),
        )

        for row in invalid_rows:
            with self.subTest(row=row), self.assertRaises(GRCProjectReferenceError):
                GRCGoldProjectSector.from_gold_row(row)


class GRCProjectSectorDependencyTest(TestCase):
    def setUp(self):
        Sector.objects.create(pk=0, title="WASH")
        SectorTag.objects.create(pk=0, title="WASH")

    def test_accepts_existing_upstream_go_sector_ids(self):
        validate_grc_project_sectors([GRCGoldProjectSector.from_gold_row(sector_row())])

    def test_rejects_missing_upstream_primary_or_secondary_id(self):
        missing_primary = GRCGoldProjectSector.from_gold_row(
            sector_row(goprojectprimarysectorid=99)
        )
        missing_secondary = GRCGoldProjectSector.from_gold_row(
            sector_row(goprojectsecondarysectortagid=99)
        )

        for sector in (missing_primary, missing_secondary):
            with self.subTest(sector=sector), self.assertRaises(GRCProjectReferenceError):
                validate_grc_project_sectors([sector])

    def test_rejects_duplicate_mappings(self):
        duplicate_source = GRCGoldProjectSector.from_gold_row(
            sector_row(goprojectprimarysectorid=1, goprojectsecondarysectortagid=None)
        )
        duplicate_primary = GRCGoldProjectSector.from_gold_row(
            sector_row(sectorkey=11, goprojectsecondarysectortagid=None)
        )
        duplicate_secondary = GRCGoldProjectSector.from_gold_row(
            sector_row(sectorkey=11, goprojectprimarysectorid=1)
        )

        duplicate_batches = (
            [GRCGoldProjectSector.from_gold_row(sector_row()), duplicate_source],
            [GRCGoldProjectSector.from_gold_row(sector_row()), duplicate_primary],
            [GRCGoldProjectSector.from_gold_row(sector_row()), duplicate_secondary],
        )
        for sectors in duplicate_batches:
            with self.subTest(sectors=sectors), self.assertRaises(GRCProjectReferenceError):
                validate_grc_project_sectors(sectors)
