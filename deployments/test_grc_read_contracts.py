from api.factories.country import CountryFactory
from api.factories.district import DistrictFactory
from api.factories.event import EventFactory
from deployments.factories.emergency_project import (
    EmergencyProjectActivityActionFactory,
    EmergencyProjectActivityFactory,
    EmergencyProjectActivitySectorFactory,
    EmergencyProjectFactory,
)
from deployments.factories.project import ProjectFactory, SectorFactory, SectorTagFactory
from deployments.models import OperationTypes, ProgrammeTypes, Statuses, VisibilityCharChoices
from main.test_case import APITestCase


GRC_PAGINATION_FIELDS = {
    "count",
    "next",
    "previous",
    "results",
}

GRC_ACTIVITY_DISAGGREGATION_FIELDS = {
    "male_0_1_count",
    "male_2_5_count",
    "male_6_12_count",
    "male_13_17_count",
    "male_18_59_count",
    "male_60_plus_count",
    "male_unknown_age_count",
    "female_0_1_count",
    "female_2_5_count",
    "female_6_12_count",
    "female_13_17_count",
    "female_18_59_count",
    "female_60_plus_count",
    "female_unknown_age_count",
    "other_0_1_count",
    "other_2_5_count",
    "other_6_12_count",
    "other_13_17_count",
    "other_18_59_count",
    "other_60_plus_count",
    "other_unknown_age_count",
}


def assert_grc_contract_fields(test_case, payload, expected_fields):
    missing_fields = set(expected_fields) - set(payload)
    test_case.assertFalse(missing_fields, f"Missing GRC read-contract fields: {sorted(missing_fields)}")


class GRCProjectReadContractTest(APITestCase):
    """Characterize the retired 3W Project UI's existing API contract."""

    def setUp(self):
        super().setUp()
        self.authenticate(self.ifrc_user)
        self.country = CountryFactory(name="Germany", iso="DE", iso3="DEU")
        self.district = DistrictFactory(
            name="Berlin",
            country=self.country,
            is_deprecated=False,
        )
        self.primary_sector = SectorFactory(title="Health")
        self.secondary_sector = SectorTagFactory(title="Preparedness")
        event = EventFactory(name="Linked operation", countries=[self.country], parent_event=None)
        self.project = ProjectFactory(
            name="GRC project",
            project_country=self.country,
            reporting_ns=self.country,
            primary_sector=self.primary_sector,
            secondary_sectors=[self.secondary_sector],
            event=event,
            dtype=event.dtype,
            visibility=VisibilityCharChoices.PUBLIC,
            programme_type=ProgrammeTypes.BILATERAL,
            operation_type=OperationTypes.EMERGENCY_OPERATION,
            status=Statuses.ONGOING,
        )
        self.project.project_districts.add(self.district)

    def test_project_list_contract(self):
        response = self.client.get(
            f"/api/v2/project/?limit=10&country={self.country.id}&reporting_ns={self.country.id}"
        )

        self.assert_200(response)
        payload = response.json()
        assert_grc_contract_fields(self, payload, GRC_PAGINATION_FIELDS)
        project_payload = next(item for item in payload["results"] if item["id"] == self.project.id)
        assert_grc_contract_fields(
            self,
            project_payload,
            {
                "id",
                "name",
                "modified_at",
                "project_country",
                "project_country_detail",
                "project_districts_detail",
                "reporting_ns",
                "reporting_ns_detail",
                "operation_type",
                "programme_type",
                "primary_sector_display",
                "primary_sector",
                "secondary_sectors",
                "budget_amount",
                "programme_type_display",
                "dtype_detail",
                "status",
                "status_display",
                "target_total",
                "reached_total",
            },
        )
        assert_grc_contract_fields(self, project_payload["project_country_detail"], {"id", "name", "iso3"})
        assert_grc_contract_fields(self, project_payload["reporting_ns_detail"], {"id", "society_name", "iso3"})
        assert_grc_contract_fields(self, project_payload["dtype_detail"], {"id", "name"})
        district_payload = next(
            item for item in project_payload["project_districts_detail"] if item["id"] == self.district.id
        )
        assert_grc_contract_fields(self, district_payload, {"id", "name"})

    def test_primary_sector_lookup_contract(self):
        response = self.client.get("/api/v2/primarysector")

        self.assert_200(response)
        sector_payload = next(item for item in response.json() if item["key"] == self.primary_sector.id)
        assert_grc_contract_fields(self, sector_payload, {"key", "label"})

    def test_secondary_sector_lookup_contract(self):
        response = self.client.get("/api/v2/secondarysector")

        self.assert_200(response)
        sector_payload = next(item for item in response.json() if item["key"] == self.secondary_sector.id)
        assert_grc_contract_fields(self, sector_payload, {"key", "label"})

    def test_project_detail_contract(self):
        response = self.client.get(f"/api/v2/project/{self.project.id}/")

        self.assert_200(response)
        project_payload = response.json()
        assert_grc_contract_fields(
            self,
            project_payload,
            {
                "id",
                "name",
                "modified_at",
                "modified_by_detail",
                "project_country_detail",
                "project_districts_detail",
                "reporting_ns_detail",
                "reporting_ns_contact_name",
                "reporting_ns_contact_role",
                "reporting_ns_contact_email",
                "operation_type_display",
                "programme_type_display",
                "event_detail",
                "dtype_detail",
                "primary_sector_display",
                "secondary_sectors_display",
                "start_date",
                "end_date",
                "status_display",
                "annual_splits",
                "budget_amount",
                "target_male",
                "target_female",
                "target_other",
                "target_total",
                "reached_male",
                "reached_female",
                "reached_other",
                "reached_total",
            },
        )
        assert_grc_contract_fields(self, project_payload["event_detail"], {"id", "name"})


class GRCActivityReadContractTest(APITestCase):
    """Characterize the existing 3W Activity and Operation activity-tab contract."""

    def setUp(self):
        super().setUp()
        self.authenticate(self.ifrc_user)
        country = CountryFactory(name="Germany", iso="DE", iso3="DEU")
        self.event = EventFactory(name="GRC operation", countries=[country], parent_event=None)
        sector = EmergencyProjectActivitySectorFactory(title="Health")
        action = EmergencyProjectActivityActionFactory(title="Primary health care", sector=sector)
        self.project = EmergencyProjectFactory(
            title="GRC activity container",
            country=country,
            reporting_ns=country,
            event=self.event,
            visibility=VisibilityCharChoices.PUBLIC,
        )
        self.activity = EmergencyProjectActivityFactory(
            project=self.project,
            sector=sector,
            action=action,
            people_count=100,
            male_count=45,
            female_count=55,
            people_households="people",
            custom_supplies={},
        )

    def assert_project_contract(self, project_payload):
        assert_grc_contract_fields(
            self,
            project_payload,
            {
                "id",
                "title",
                "start_date",
                "end_date",
                "status",
                "status_display",
                "country",
                "country_details",
                "event",
                "event_details",
                "activity_lead",
                "activity_lead_display",
                "reporting_ns",
                "reporting_ns_details",
                "deployed_eru",
                "deployed_eru_details",
                "districts",
                "districts_details",
                "activities",
                "modified_at",
                "modified_by_details",
            },
        )
        activity_payload = next(item for item in project_payload["activities"] if item["id"] == self.activity.id)
        assert_grc_contract_fields(
            self,
            activity_payload,
            {
                "id",
                "sector",
                "sector_details",
                "action",
                "action_details",
                "custom_action",
                "details",
                "is_simplified_report",
                "people_households",
                "household_count",
                "people_count",
                "male_count",
                "female_count",
                "supplies",
                "custom_supplies",
            }
            | GRC_ACTIVITY_DISAGGREGATION_FIELDS,
        )
        assert_grc_contract_fields(self, activity_payload["sector_details"], {"id", "title"})
        assert_grc_contract_fields(self, activity_payload["action_details"], {"id", "title"})

    def test_activity_list_contract(self):
        response = self.client.get(f"/api/v2/emergency-project/?limit=10&event={self.event.id}")

        self.assert_200(response)
        payload = response.json()
        assert_grc_contract_fields(self, payload, GRC_PAGINATION_FIELDS)
        project_payload = next(item for item in payload["results"] if item["id"] == self.project.id)
        self.assert_project_contract(project_payload)

    def test_activity_detail_contract(self):
        response = self.client.get(f"/api/v2/emergency-project/{self.project.id}/")

        self.assert_200(response)
        self.assert_project_contract(response.json())
