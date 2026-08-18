from datetime import timedelta

from django.utils import timezone

from api.factories.country import CountryFactory
from api.factories.district import DistrictFactory
from api.factories.event import AppealFactory, EventFactory
from api.models import AppealStatus, AppealType, VisibilityChoices
from main.test_case import APITestCase


GRC_PAGINATION_FIELDS = {
    "count",
    "next",
    "previous",
    "results",
}


def assert_grc_contract_fields(test_case, payload, expected_fields):
    missing_fields = set(expected_fields) - set(payload)
    test_case.assertFalse(missing_fields, f"Missing GRC read-contract fields: {sorted(missing_fields)}")


class GRCCountryReadContractTest(APITestCase):
    """Characterize the existing Country list and detail wire contracts."""

    def setUp(self):
        super().setUp()
        self.authenticate(self.ifrc_user)
        self.country = CountryFactory(name="Germany", iso="DE", iso3="DEU")
        self.district = DistrictFactory(
            name="Berlin",
            country=self.country,
            is_deprecated=False,
        )

    def test_country_list_contract(self):
        response = self.client.get("/api/v2/country/?limit=10")

        self.assert_200(response)
        payload = response.json()
        assert_grc_contract_fields(self, payload, GRC_PAGINATION_FIELDS)
        country_payload = next(item for item in payload["results"] if item["id"] == self.country.id)
        assert_grc_contract_fields(
            self,
            country_payload,
            {
                "id",
                "name",
                "iso",
                "iso3",
                "society_name",
                "region",
                "record_type",
                "bbox",
                "centroid",
                "independent",
                "is_deprecated",
                "links",
            },
        )

    def test_country_detail_contract(self):
        response = self.client.get(f"/api/v2/country/{self.country.id}/")

        self.assert_200(response)
        assert_grc_contract_fields(
            self,
            response.json(),
            {
                "id",
                "name",
                "iso",
                "iso3",
                "society_name",
                "region",
                "regions_details",
                "bbox",
                "centroid",
                "independent",
                "sovereign_state_id",
                "additional_tab_name",
                "links",
                "contacts",
            },
        )

    def test_country_district_list_contract(self):
        response = self.client.get(f"/api/v2/district/?limit=10&country={self.country.id}")

        self.assert_200(response)
        payload = response.json()
        assert_grc_contract_fields(self, payload, GRC_PAGINATION_FIELDS)
        district_payload = next(item for item in payload["results"] if item["id"] == self.district.id)
        assert_grc_contract_fields(
            self,
            district_payload,
            {
                "id",
                "name",
                "centroid",
            },
        )


class GRCOperationReadContractTest(APITestCase):
    """Characterize Event/Appeal responses consumed by Operation pages."""

    def setUp(self):
        super().setUp()
        self.authenticate(self.ifrc_user)
        self.country = CountryFactory(name="Germany", iso="DE", iso3="DEU")
        self.event = EventFactory(
            name="GRC operation",
            countries=[self.country],
            parent_event=None,
            visibility=VisibilityChoices.PUBLIC,
        )

    def test_event_list_contract(self):
        response = self.client.get("/api/v2/event/?limit=10")

        self.assert_200(response)
        payload = response.json()
        assert_grc_contract_fields(self, payload, GRC_PAGINATION_FIELDS)
        event_payload = next(item for item in payload["results"] if item["id"] == self.event.id)
        assert_grc_contract_fields(
            self,
            event_payload,
            {
                "id",
                "name",
                "dtype",
                "countries",
                "summary",
                "num_affected",
                "glide",
                "disaster_start_date",
                "appeals",
                "field_reports",
                "ifrc_severity_level",
                "ifrc_severity_level_display",
                "active_deployments",
            },
        )

    def test_event_detail_contract(self):
        response = self.client.get(f"/api/v2/event/{self.event.id}/")

        self.assert_200(response)
        assert_grc_contract_fields(
            self,
            response.json(),
            {
                "id",
                "name",
                "dtype",
                "countries",
                "districts",
                "summary",
                "num_affected",
                "disaster_start_date",
                "appeals",
                "contacts",
                "key_figures",
                "field_reports",
                "ifrc_severity_level",
                "ifrc_severity_level_display",
                "ifrc_severity_level_update_date",
                "glide",
                "links",
                "featured_documents",
                "hide_attached_field_reports",
                "hide_field_report_map",
                "response_activity_count",
                "visibility",
                "active_deployments",
            },
        )

    def test_appeal_list_contract(self):
        now = timezone.now()
        appeal = AppealFactory(
            aid="MDRDE001",
            code="MDRDE001",
            event=self.event,
            country=self.country,
            region=self.country.region,
            atype=AppealType.APPEAL,
            status=AppealStatus.ACTIVE,
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=30),
        )

        response = self.client.get(f"/api/v2/appeal/?limit=10&country={self.country.id}&has_event=true")

        self.assert_200(response)
        payload = response.json()
        assert_grc_contract_fields(self, payload, GRC_PAGINATION_FIELDS)
        appeal_payload = next(item for item in payload["results"] if item["id"] == str(appeal.id))
        assert_grc_contract_fields(
            self,
            appeal_payload,
            {
                "id",
                "aid",
                "name",
                "dtype",
                "atype",
                "atype_display",
                "status",
                "status_display",
                "code",
                "num_beneficiaries",
                "amount_requested",
                "amount_funded",
                "start_date",
                "end_date",
                "event",
                "event_details",
                "country",
                "region",
                "needs_confirmation",
            },
        )

    def test_appeal_aggregate_contract(self):
        now = timezone.now()
        AppealFactory(
            aid="MDRDE002",
            code="MDRDE002",
            event=self.event,
            country=self.country,
            region=self.country.region,
            atype=AppealType.APPEAL,
            status=AppealStatus.ACTIVE,
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=30),
        )

        response = self.client.get(f"/api/v2/appeal/aggregated?country={self.country.id}")

        self.assert_200(response)
        assert_grc_contract_fields(
            self,
            response.json(),
            {
                "active_drefs",
                "active_appeals",
                "total_appeals",
                "target_population",
                "amount_requested",
                "amount_requested_dref_included",
                "amount_funded",
                "amount_funded_dref_included",
            },
        )
