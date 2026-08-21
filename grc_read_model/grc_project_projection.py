import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from api.models import Country, DisasterType, District, Event, VisibilityCharChoices
from deployments.models import OperationTypes, ProgrammeTypes, Project, Sector, SectorTag
from grc_read_model.grc_project_reference import GRCProjectControlledValues
from grc_read_model.models import GRCEntityType, GRCSourceRecord, GRCSyncRun


class GRCProjectProjectionError(ValueError):
    pass


_MAX_GO_INTEGER = 2_147_483_647


def _required_int(row: Mapping[str, object], field: str, *, allow_zero: bool = False) -> int:
    value = row.get(field)
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "non-negative" if allow_zero else "positive"
        raise GRCProjectProjectionError(f"{field} must be a {qualifier} integer")
    return value


def _optional_int(row: Mapping[str, object], field: str, *, allow_zero: bool = False) -> int | None:
    if row.get(field) is None:
        return None
    return _required_int(row, field, allow_zero=allow_zero)


def _optional_count(row: Mapping[str, object], field: str) -> int | None:
    value = row.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > _MAX_GO_INTEGER:
        raise GRCProjectProjectionError(f"{field} must be a non-negative GO integer or null")
    return value


def _optional_amount(row: Mapping[str, object], field: str) -> int | None:
    value = row.get(field)
    if value is None:
        return None
    if isinstance(value, bool):
        raise GRCProjectProjectionError(f"{field} must be a whole non-negative CHF amount or null")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise GRCProjectProjectionError(f"{field} must be a whole non-negative CHF amount or null") from None
    if amount < 0 or amount != amount.to_integral_value() or amount > _MAX_GO_INTEGER:
        raise GRCProjectProjectionError(
            f"{field} cannot be represented by the upstream GO integer field without loss"
        )
    return int(amount)


def _required_string(row: Mapping[str, object], field: str, max_length: int) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise GRCProjectProjectionError(f"{field} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise GRCProjectProjectionError(f"{field} exceeds the GO maximum length of {max_length}")
    return normalized


def _optional_string(row: Mapping[str, object], field: str, max_length: int) -> str | None:
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise GRCProjectProjectionError(f"{field} must be a string or null")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise GRCProjectProjectionError(f"{field} exceeds the GO maximum length of {max_length}")
    return normalized or None


def _required_datetime(row: Mapping[str, object], field: str) -> datetime:
    value = row.get(field)
    if not isinstance(value, datetime):
        raise GRCProjectProjectionError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise GRCProjectProjectionError(f"{field} must be timezone-aware")
    return value


def _integer_tuple(row: Mapping[str, object], field: str, *, allow_zero: bool = False) -> tuple[int, ...]:
    value = row.get(field)
    if not isinstance(value, (list, tuple)):
        raise GRCProjectProjectionError(f"{field} must be a list or tuple of GO IDs")
    normalized = tuple(_required_int({field: item}, field, allow_zero=allow_zero) for item in value)
    if len(normalized) != len(set(normalized)):
        raise GRCProjectProjectionError(f"{field} must not contain duplicate GO IDs")
    return normalized


@dataclass(frozen=True)
class GRCGoldProject:
    project_id: int
    name: str
    reporting_ns_country_id: int
    project_country_id: int
    district_ids: tuple[int, ...]
    event_id: int | None
    disaster_type_id: int | None
    primary_sector_id: int
    secondary_sector_tag_ids: tuple[int, ...]
    controlled_values: GRCProjectControlledValues
    budget_amount: int | None
    target_total: int | None
    reached_total: int | None
    reporting_contact_name: str | None
    reporting_contact_role: str | None
    reporting_contact_email: str | None
    ingested_at: datetime

    @classmethod
    def from_gold_row(cls, row: Mapping[str, object]) -> "GRCGoldProject":
        event_id = _optional_int(row, "goeventid")
        disaster_type_id = _optional_int(row, "godisastertypeid")
        if (event_id is None) != (disaster_type_id is None):
            raise GRCProjectProjectionError(
                "goeventid and godisastertypeid must either both resolve through the primary Operation or both be null"
            )

        project = cls(
            project_id=_required_int(row, "projectid"),
            name=_required_string(row, "projectname", 500),
            reporting_ns_country_id=_required_int(row, "goreportingnscountryid"),
            project_country_id=_required_int(row, "goprojectcountryid"),
            district_ids=_integer_tuple(row, "godistrictids"),
            event_id=event_id,
            disaster_type_id=disaster_type_id,
            primary_sector_id=_required_int(row, "goprojectprimarysectorid", allow_zero=True),
            secondary_sector_tag_ids=_integer_tuple(
                row,
                "goprojectsecondarysectortagids",
                allow_zero=True,
            ),
            controlled_values=GRCProjectControlledValues.from_gold_row(row),
            budget_amount=_optional_amount(row, "budgetamountchf"),
            target_total=_optional_count(row, "peopletargeted"),
            reached_total=_optional_count(row, "peoplereached"),
            reporting_contact_name=_optional_string(row, "reportingcontactname", 255),
            reporting_contact_role=_optional_string(row, "reportingcontactrole", 255),
            reporting_contact_email=_optional_string(row, "reportingcontactemail", 255),
            ingested_at=_required_datetime(row, "ingestedat"),
        )
        if (
            project.controlled_values.operation_type == OperationTypes.EMERGENCY_OPERATION
            and project.controlled_values.programme_type == ProgrammeTypes.MULTILATERAL
            and project.event_id is None
        ):
            raise GRCProjectProjectionError(
                "a multilateral Emergency Operation Project requires a primary Operation linked to a GO Event"
            )
        return project

    def content_hash(self) -> str:
        content = {
            "budget_amount": self.budget_amount,
            "controlled_values": {
                "end_date": self.controlled_values.end_date.isoformat(),
                "operation_type": self.controlled_values.operation_type,
                "programme_type": self.controlled_values.programme_type,
                "start_date": self.controlled_values.start_date.isoformat(),
                "status": self.controlled_values.status,
            },
            "disaster_type_id": self.disaster_type_id,
            "district_ids": sorted(self.district_ids),
            "event_id": self.event_id,
            "name": self.name,
            "primary_sector_id": self.primary_sector_id,
            "project_country_id": self.project_country_id,
            "project_id": self.project_id,
            "reached_total": self.reached_total,
            "reporting_contact_email": self.reporting_contact_email,
            "reporting_contact_name": self.reporting_contact_name,
            "reporting_contact_role": self.reporting_contact_role,
            "reporting_ns_country_id": self.reporting_ns_country_id,
            "secondary_sector_tag_ids": sorted(self.secondary_sector_tag_ids),
            "target_total": self.target_total,
        }
        serialized = json.dumps(content, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def project_defaults(self, as_of: date) -> dict[str, object]:
        return {
            **self.controlled_values.project_defaults(as_of),
            "name": self.name,
            "reporting_ns_id": self.reporting_ns_country_id,
            "project_country_id": self.project_country_id,
            "event_id": self.event_id,
            "dtype_id": self.disaster_type_id,
            "primary_sector_id": self.primary_sector_id,
            "budget_amount": self.budget_amount,
            "actual_expenditure": None,
            "target_male": None,
            "target_female": None,
            "target_other": None,
            "target_total": self.target_total,
            "reached_male": None,
            "reached_female": None,
            "reached_other": None,
            "reached_total": self.reached_total,
            "reporting_ns_contact_name": self.reporting_contact_name,
            "reporting_ns_contact_role": self.reporting_contact_role,
            "reporting_ns_contact_email": self.reporting_contact_email,
            "user": None,
            "modified_by": None,
            "visibility": VisibilityCharChoices.MEMBERSHIP,
        }


@dataclass(frozen=True)
class GRCGoldProjectDeletion:
    project_id: int
    ingested_at: datetime

    @classmethod
    def from_gold_row(cls, row: Mapping[str, object]) -> "GRCGoldProjectDeletion":
        return cls(
            project_id=_required_int(row, "projectid"),
            ingested_at=_required_datetime(row, "ingestedat"),
        )

    def content_hash(self) -> str:
        return hashlib.sha256(f"deleted:{self.project_id}".encode("ascii")).hexdigest()


@dataclass(frozen=True)
class GRCProjectPublicationResult:
    rows_seen: int
    rows_created: int
    rows_updated: int
    rows_deleted: int


def _validate_project_batch(
    records: Sequence[GRCGoldProject],
    deletions: Sequence[GRCGoldProjectDeletion],
    *,
    as_of: date,
) -> None:
    project_ids = [record.project_id for record in records]
    deleted_ids = [record.project_id for record in deletions]
    if len(project_ids) != len(set(project_ids)):
        raise GRCProjectProjectionError("duplicate projectid in active Project snapshot")
    if len(deleted_ids) != len(set(deleted_ids)):
        raise GRCProjectProjectionError("duplicate projectid in Project deletion snapshot")
    overlap = set(project_ids) & set(deleted_ids)
    if overlap:
        values = ", ".join(str(value) for value in sorted(overlap))
        raise GRCProjectProjectionError(f"Project IDs cannot be both active and deleted: {values}")

    for record in records:
        record.controlled_values.project_defaults(as_of)


def _validate_project_dependencies(records: Sequence[GRCGoldProject]) -> None:
    country_ids = {
        country_id
        for record in records
        for country_id in (record.project_country_id, record.reporting_ns_country_id)
    }
    countries = Country.objects.in_bulk(country_ids)
    missing_country_ids = country_ids - set(countries)
    if missing_country_ids:
        values = ", ".join(str(value) for value in sorted(missing_country_ids))
        raise GRCProjectProjectionError(f"Country projection must run first; missing GO ID(s): {values}")

    district_ids = {district_id for record in records for district_id in record.district_ids}
    districts = District.objects.in_bulk(district_ids)
    missing_district_ids = district_ids - set(districts)
    if missing_district_ids:
        values = ", ".join(str(value) for value in sorted(missing_district_ids))
        raise GRCProjectProjectionError(f"District projection must run first; missing GO ID(s): {values}")
    for record in records:
        outside_country_ids = sorted(
            district_id
            for district_id in record.district_ids
            if districts[district_id].country_id != record.project_country_id
        )
        if outside_country_ids:
            values = ", ".join(str(value) for value in outside_country_ids)
            raise GRCProjectProjectionError(
                f"Project {record.project_id} Districts must belong to its Project Country; invalid GO ID(s): {values}"
            )

    primary_sector_ids = {record.primary_sector_id for record in records}
    missing_primary_sector_ids = primary_sector_ids - set(Sector.objects.in_bulk(primary_sector_ids))
    if missing_primary_sector_ids:
        values = ", ".join(str(value) for value in sorted(missing_primary_sector_ids))
        raise GRCProjectProjectionError(f"missing upstream GO Project Sector ID(s): {values}")

    secondary_sector_ids = {
        sector_id for record in records for sector_id in record.secondary_sector_tag_ids
    }
    missing_secondary_sector_ids = secondary_sector_ids - set(SectorTag.objects.in_bulk(secondary_sector_ids))
    if missing_secondary_sector_ids:
        values = ", ".join(str(value) for value in sorted(missing_secondary_sector_ids))
        raise GRCProjectProjectionError(f"missing upstream GO Project SectorTag ID(s): {values}")

    event_ids = {record.event_id for record in records if record.event_id is not None}
    events = Event.objects.in_bulk(event_ids)
    missing_event_ids = event_ids - set(events)
    if missing_event_ids:
        values = ", ".join(str(value) for value in sorted(missing_event_ids))
        raise GRCProjectProjectionError(f"Event projection must run first; missing GO ID(s): {values}")

    disaster_type_ids = {
        record.disaster_type_id for record in records if record.disaster_type_id is not None
    }
    missing_disaster_type_ids = disaster_type_ids - set(DisasterType.objects.in_bulk(disaster_type_ids))
    if missing_disaster_type_ids:
        values = ", ".join(str(value) for value in sorted(missing_disaster_type_ids))
        raise GRCProjectProjectionError(f"missing upstream GO DisasterType ID(s): {values}")

    mismatched_events = sorted(
        record.event_id
        for record in records
        if record.event_id is not None and events[record.event_id].dtype_id != record.disaster_type_id
    )
    if mismatched_events:
        values = ", ".join(str(value) for value in mismatched_events)
        raise GRCProjectProjectionError(f"Project primary Operation/Event disaster type mismatch for GO Event ID(s): {values}")


def publish_grc_project_records(
    records: Sequence[GRCGoldProject],
    deletions: Sequence[GRCGoldProjectDeletion],
    sync_run: GRCSyncRun,
    *,
    as_of: date,
    source_system: str = "grc_gold",
) -> GRCProjectPublicationResult:
    """Publish one complete Project fact snapshot through the existing GO Project model."""

    if not source_system.strip():
        raise GRCProjectProjectionError("source_system must not be empty")
    if sync_run.pk is None or sync_run.status != GRCSyncRun.Status.RUNNING:
        raise GRCProjectProjectionError("sync_run must be a saved running GRC sync run")
    if isinstance(as_of, datetime) or not isinstance(as_of, date):
        raise GRCProjectProjectionError("as_of must be a date")

    source_system = source_system.strip()
    records = tuple(records)
    deletions = tuple(deletions)
    if any(not isinstance(record, GRCGoldProject) for record in records):
        raise GRCProjectProjectionError("records must contain only GRCGoldProject records")
    if any(not isinstance(record, GRCGoldProjectDeletion) for record in deletions):
        raise GRCProjectProjectionError("deletions must contain only GRCGoldProjectDeletion records")

    _validate_project_batch(records, deletions, as_of=as_of)
    _validate_project_dependencies(records)

    created_count = 0
    with transaction.atomic():
        target_content_type = ContentType.objects.get_for_model(Project)
        published_at = timezone.now()

        for deletion in deletions:
            Project.objects.filter(pk=deletion.project_id).delete()
            GRCSourceRecord.objects.update_or_create(
                source_system=source_system,
                entity_type=GRCEntityType.PROJECT,
                source_id=str(deletion.project_id),
                defaults={
                    "source_ingested_at": deletion.ingested_at,
                    "content_hash": deletion.content_hash(),
                    "is_deleted": True,
                    "target_content_type": target_content_type,
                    "target_object_id": deletion.project_id,
                    "last_successful_run": sync_run,
                    "published_at": published_at,
                },
            )

        for record in records:
            project, created = Project.objects.update_or_create(
                pk=record.project_id,
                defaults=record.project_defaults(as_of),
            )
            created_count += int(created)
            project.project_districts.set(record.district_ids)
            project.secondary_sectors.set(record.secondary_sector_tag_ids)
            project.annual_splits.all().delete()
            Project.objects.filter(pk=project.pk).update(
                status=record.controlled_values.status,
                modified_at=record.ingested_at,
            )

            GRCSourceRecord.objects.update_or_create(
                source_system=source_system,
                entity_type=GRCEntityType.PROJECT,
                source_id=str(record.project_id),
                defaults={
                    "source_ingested_at": record.ingested_at,
                    "content_hash": record.content_hash(),
                    "is_deleted": False,
                    "target_content_type": target_content_type,
                    "target_object_id": record.project_id,
                    "last_successful_run": sync_run,
                    "published_at": published_at,
                },
            )

    return GRCProjectPublicationResult(
        rows_seen=len(records) + len(deletions),
        rows_created=created_count,
        rows_updated=len(records) - created_count,
        rows_deleted=len(deletions),
    )
