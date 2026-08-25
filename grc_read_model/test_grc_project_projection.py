from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from django.test import SimpleTestCase, TestCase

from api.factories.country import CountryFactory
from api.factories.disaster_type import DisasterTypeFactory
from api.factories.district import DistrictFactory
from api.factories.event import EventFactory
from deployments.models import AnnualSplit, OperationTypes, ProgrammeTypes, Project, Sector, SectorTag, Statuses
from grc_read_model.grc_identity import publish_grc_source_record
from grc_read_model.grc_project_projection import (
    GRCGoldProject,
    GRCGoldProjectDeletion,
    GRCProjectProjectionError,
    publish_grc_project_records,
)
from grc_read_model.models import GRCEntityType, GRCSourceRecord, GRCSyncRun


COUNTRY_SOURCE_ID = UUID("10000000-0000-0000-0000-000000000001")
MISSING_COUNTRY_SOURCE_ID = UUID("10000000-0000-0000-0000-000000000099")
DISTRICT_SOURCE_ID = UUID("20000000-0000-0000-0000-000000000001")
EVENT_SOURCE_ID = UUID("30000000-0000-0000-0000-000000000001")
PROJECT_SOURCE_ID = UUID("40000000-0000-0000-0000-000000000001")
UNKNOWN_PROJECT_SOURCE_ID = UUID("40000000-0000-0000-0000-000000000099")


def project_row(**overrides):
    row = {
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
        "ingestedat": datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


class GRCGoldProjectTest(SimpleTestCase):
    def test_maps_approved_gold_contract_without_guessing(self):
        project = GRCGoldProject.from_gold_row(project_row())

        self.assertEqual(project.project_key, 7001)
        self.assertEqual(project.source_id, PROJECT_SOURCE_ID)
        self.assertEqual(project.budget_amount, 250000)
        self.assertEqual(project.district_source_ids, (DISTRICT_SOURCE_ID,))
        self.assertEqual(project.secondary_sector_tag_ids, (1,))
        self.assertEqual(project.reporting_ns_country_source_id, COUNTRY_SOURCE_ID)

    def test_rejects_lossy_or_semantically_incomplete_values(self):
        invalid_rows = (
            project_row(projectname=""),
            project_row(budgetamountchf=Decimal("10.50")),
            project_row(ingestedat=datetime(2026, 8, 21, 8, 0)),
            project_row(district_grc_source_ids=[DISTRICT_SOURCE_ID, DISTRICT_SOURCE_ID]),
            project_row(event_grc_source_id=EVENT_SOURCE_ID, godisastertypeid=None),
            project_row(peopletargeted=-1),
        )
        for row in invalid_rows:
            with self.subTest(row=row), self.assertRaises((GRCProjectProjectionError, ValueError)):
                GRCGoldProject.from_gold_row(row)

    def test_requires_event_for_multilateral_emergency_project(self):
        with self.assertRaisesRegex(GRCProjectProjectionError, "requires a primary Operation"):
            GRCGoldProject.from_gold_row(
                project_row(
                    event_grc_source_id=None,
                    godisastertypeid=None,
                    goprojectprogrammetypeid=ProgrammeTypes.MULTILATERAL,
                    goprojectoperationtypeid=OperationTypes.EMERGENCY_OPERATION,
                )
            )


class GRCProjectPublicationTest(TestCase):
    def setUp(self):
        self.country = CountryFactory(id=276, iso="DE", iso3="DEU")
        self.district = DistrictFactory(id=1001, country=self.country)
        self.disaster_type = DisasterTypeFactory(id=5)
        self.event = EventFactory(id=3001, dtype=self.disaster_type)
        Sector.objects.create(pk=0, title="WASH")
        SectorTag.objects.create(pk=1, title="Health")
        self.sync_run = GRCSyncRun.objects.create(pipeline="test_project")
        for entity_type, source_id, model, target_id in (
            (GRCEntityType.COUNTRY, COUNTRY_SOURCE_ID, type(self.country), self.country.pk),
            (GRCEntityType.DISTRICT, DISTRICT_SOURCE_ID, type(self.district), self.district.pk),
            (GRCEntityType.DISASTER_EVENT, EVENT_SOURCE_ID, type(self.event), self.event.pk),
        ):
            publish_grc_source_record(
                source_system="grc_gold",
                entity_type=entity_type,
                source_id=source_id,
                target_model=model,
                target_object_id=target_id,
                source_ingested_at=None,
                content_hash="a" * 64,
                is_deleted=False,
                sync_run=self.sync_run,
            )

    def test_publishes_existing_go_project_contract_and_metadata(self):
        record = GRCGoldProject.from_gold_row(project_row())

        result = publish_grc_project_records(
            [record],
            [],
            self.sync_run,
            as_of=date(2026, 8, 21),
        )

        project = Project.objects.get(pk=7001)
        self.assertEqual(result.rows_created, 1)
        self.assertEqual(project.name, "Flood recovery")
        self.assertEqual(project.reporting_ns_id, 276)
        self.assertEqual(project.project_country_id, 276)
        self.assertEqual(project.event_id, 3001)
        self.assertEqual(project.dtype_id, 5)
        self.assertEqual(project.budget_amount, 250000)
        self.assertEqual(project.target_total, 1000)
        self.assertEqual(project.reached_total, 750)
        self.assertEqual(list(project.project_districts.values_list("id", flat=True)), [1001])
        self.assertEqual(list(project.secondary_sectors.values_list("id", flat=True)), [1])
        self.assertIsNone(project.actual_expenditure)
        self.assertIsNone(project.modified_by)

        source_record = GRCSourceRecord.objects.get(
            entity_type=GRCEntityType.PROJECT,
            source_id=str(PROJECT_SOURCE_ID),
        )
        self.assertFalse(source_record.is_deleted)
        self.assertEqual(source_record.target_object_id, 7001)
        self.assertEqual(source_record.content_hash, record.content_hash())

    def test_rolls_back_when_dependency_is_not_projected(self):
        record = GRCGoldProject.from_gold_row(
            project_row(project_country_grc_source_id=MISSING_COUNTRY_SOURCE_ID)
        )

        with self.assertRaisesRegex(ValueError, "missing published country source GUID"):
            publish_grc_project_records(
                [record],
                [],
                self.sync_run,
                as_of=date(2026, 8, 21),
            )

        self.assertFalse(Project.objects.filter(pk=7001).exists())

    def test_removes_stale_annual_splits_so_overall_gold_totals_are_visible(self):
        record = GRCGoldProject.from_gold_row(project_row())
        publish_grc_project_records(
            [record],
            [],
            self.sync_run,
            as_of=date(2026, 8, 21),
        )
        AnnualSplit.objects.create(project_id=7001, year=2025, target_total=999)

        publish_grc_project_records(
            [record],
            [],
            self.sync_run,
            as_of=date(2026, 8, 21),
        )

        self.assertFalse(AnnualSplit.objects.filter(project_id=7001).exists())

    def test_applies_gold_soft_delete_idempotently(self):
        record = GRCGoldProject.from_gold_row(project_row())
        publish_grc_project_records(
            [record],
            [],
            self.sync_run,
            as_of=date(2026, 8, 21),
        )
        deletion = GRCGoldProjectDeletion.from_gold_row(
            {
                "grc_source_id": PROJECT_SOURCE_ID,
                "projectid": 7001,
                "goprojectid": 7001,
                "ingestedat": datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc),
            }
        )

        result = publish_grc_project_records(
            [],
            [deletion],
            self.sync_run,
            as_of=date(2026, 8, 21),
        )
        publish_grc_project_records(
            [],
            [deletion],
            self.sync_run,
            as_of=date(2026, 8, 21),
        )

        self.assertEqual(result.rows_deleted, 1)
        self.assertFalse(Project.objects.filter(pk=7001).exists())
        source_record = GRCSourceRecord.objects.get(
            entity_type=GRCEntityType.PROJECT,
            source_id=str(PROJECT_SOURCE_ID),
        )
        self.assertTrue(source_record.is_deleted)

    def test_allocates_and_retains_a_go_id_for_a_grc_only_project(self):
        record = GRCGoldProject.from_gold_row(project_row(goprojectid=None))

        first = publish_grc_project_records(
            [record], [], self.sync_run, as_of=date(2026, 8, 21)
        )
        mapping = GRCSourceRecord.objects.get(source_id=str(PROJECT_SOURCE_ID))
        second = publish_grc_project_records(
            [record], [], self.sync_run, as_of=date(2026, 8, 21)
        )

        self.assertEqual(first.rows_created, 1)
        self.assertEqual(second.rows_created, 0)
        self.assertEqual(second.rows_unchanged, 1)
        self.assertTrue(Project.objects.filter(pk=mapping.target_object_id).exists())

    def test_ignores_an_unpublished_grc_only_tombstone(self):
        deletion = GRCGoldProjectDeletion.from_gold_row(
            {
                "grc_source_id": UNKNOWN_PROJECT_SOURCE_ID,
                "projectid": 7999,
                "goprojectid": None,
                "ingestedat": datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc),
            }
        )

        result = publish_grc_project_records(
            [], [deletion], self.sync_run, as_of=date(2026, 8, 21)
        )

        self.assertEqual(result.rows_deleted, 0)
        self.assertFalse(
            GRCSourceRecord.objects.filter(source_id=str(UNKNOWN_PROJECT_SOURCE_ID)).exists()
        )
