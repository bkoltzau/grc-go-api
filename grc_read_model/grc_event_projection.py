import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from api.models import AlertLevel, Country, DisasterType, District, Event, VisibilityChoices
from grc_read_model.grc_identity import (
    advance_grc_target_sequence,
    GRCIdentityError,
    grc_source_content_matches,
    parse_grc_source_id,
    publish_grc_source_record,
    resolve_grc_target_id as _resolve_grc_target_id,
    resolve_grc_target_map as _resolve_grc_target_map,
)
from grc_read_model.models import GRCEntityType, GRCSyncRun


class GRCEventProjectionError(ValueError):
    pass


def _resolve_target_id(**kwargs) -> int | None:
    try:
        return _resolve_grc_target_id(**kwargs)
    except GRCIdentityError as exc:
        raise GRCEventProjectionError(str(exc)) from exc


def _resolve_target_map(**kwargs) -> dict[UUID, int]:
    try:
        return _resolve_grc_target_map(**kwargs)
    except GRCIdentityError as exc:
        raise GRCEventProjectionError(str(exc)) from exc


def _required_int(row: Mapping[str, object], field: str, *, allow_zero: bool = False) -> int:
    value = row.get(field)
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "non-negative" if allow_zero else "positive"
        raise GRCEventProjectionError(f"{field} must be a {qualifier} integer")
    return value


def _optional_int(row: Mapping[str, object], field: str, *, allow_zero: bool = False) -> int | None:
    if row.get(field) is None:
        return None
    return _required_int(row, field, allow_zero=allow_zero)


def _required_bool(row: Mapping[str, object], field: str) -> bool:
    value = row.get(field)
    if not isinstance(value, bool):
        raise GRCEventProjectionError(f"{field} must be a boolean")
    return value


def _required_string(row: Mapping[str, object], field: str, max_length: int) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise GRCEventProjectionError(f"{field} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise GRCEventProjectionError(f"{field} exceeds the GO maximum length of {max_length}")
    return normalized


def _optional_string(row: Mapping[str, object], field: str, max_length: int, default: str = "") -> str:
    value = row.get(field)
    if value is None:
        return default
    if not isinstance(value, str):
        raise GRCEventProjectionError(f"{field} must be a string or null")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise GRCEventProjectionError(f"{field} exceeds the GO maximum length of {max_length}")
    return normalized


def _optional_datetime(row: Mapping[str, object], field: str) -> datetime | None:
    value = row.get(field)
    if value is not None and not isinstance(value, datetime):
        raise GRCEventProjectionError(f"{field} must be a datetime or null")
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise GRCEventProjectionError(f"{field} must be timezone-aware")
    return value


def _required_datetime(row: Mapping[str, object], field: str) -> datetime:
    value = _optional_datetime(row, field)
    if value is None:
        raise GRCEventProjectionError(f"{field} must not be null")
    return value


def _uuid_tuple(row: Mapping[str, object], field: str, *, required: bool) -> tuple[UUID, ...]:
    value = row.get(field)
    if not isinstance(value, (list, tuple)):
        raise GRCEventProjectionError(f"{field} must be a list or tuple of UUIDs")
    normalized = tuple(parse_grc_source_id(item, field) for item in value)
    if required and not normalized:
        raise GRCEventProjectionError(f"{field} must contain at least one UUID")
    if len(normalized) != len(set(normalized)):
        raise GRCEventProjectionError(f"{field} must not contain duplicate UUIDs")
    return normalized


@dataclass(frozen=True)
class GRCGoldDisasterType:
    disaster_type_key: int
    go_disaster_type_id: int
    code: str
    name: str
    is_active: bool

    @classmethod
    def from_gold_row(cls, row: Mapping[str, object]) -> "GRCGoldDisasterType":
        return cls(
            disaster_type_key=_required_int(row, "disastertypekey"),
            go_disaster_type_id=_required_int(row, "godisastertypeid"),
            code=_required_string(row, "code", 50),
            name=_required_string(row, "name", 200),
            is_active=_required_bool(row, "isactive"),
        )


def validate_grc_disaster_types(
    records: Sequence[GRCGoldDisasterType],
    required_go_ids: Sequence[int] = (),
) -> None:
    records = tuple(records)
    unique_fields = {
        "disastertypekey": [record.disaster_type_key for record in records],
        "godisastertypeid": [record.go_disaster_type_id for record in records],
    }
    for field, values in unique_fields.items():
        if len(values) != len(set(values)):
            raise GRCEventProjectionError(f"duplicate {field} in dimdisastertype snapshot")

    type_ids = {record.go_disaster_type_id for record in records}
    missing_ids = type_ids - set(DisasterType.objects.in_bulk(type_ids))
    if missing_ids:
        values = ", ".join(str(value) for value in sorted(missing_ids))
        raise GRCEventProjectionError(f"missing upstream GO DisasterType ID(s): {values}")

    required_ids = set(required_go_ids)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in required_ids):
        raise GRCEventProjectionError("required_go_ids must contain positive GO DisasterType IDs")
    missing_required_ids = required_ids - type_ids
    if missing_required_ids:
        values = ", ".join(str(value) for value in sorted(missing_required_ids))
        raise GRCEventProjectionError(f"required GO DisasterType ID(s) are absent from Gold: {values}")

    active_by_id = {record.go_disaster_type_id: record.is_active for record in records}
    inactive_required_ids = sorted(value for value in required_ids if not active_by_id[value])
    if inactive_required_ids:
        values = ", ".join(str(value) for value in inactive_required_ids)
        raise GRCEventProjectionError(f"active Events cannot use inactive GO DisasterType ID(s): {values}")


@dataclass(frozen=True)
class GRCGoldDisasterEvent:
    source_id: UUID
    disaster_event_key: int
    go_event_id: int | None
    go_disaster_type_id: int
    name: str
    glide: str
    disaster_start_at: datetime
    description: str
    people_affected: int | None
    ifrc_severity_level: int
    ifrc_severity_level_updated_at: datetime | None
    country_source_ids: tuple[UUID, ...]
    district_source_ids: tuple[UUID, ...]
    is_active: bool
    source_updated_at: datetime | None
    ingested_at: datetime | None

    @classmethod
    def from_gold_row(cls, row: Mapping[str, object]) -> "GRCGoldDisasterEvent":
        severity = _required_int(row, "goifrcseveritylevelid", allow_zero=True)
        if severity not in AlertLevel.values:
            supported = ", ".join(str(value) for value in AlertLevel.values)
            raise GRCEventProjectionError(f"goifrcseveritylevelid must be an exact GO value ({supported})")

        return cls(
            source_id=parse_grc_source_id(row.get("grc_source_id")),
            disaster_event_key=_required_int(row, "disastereventkey"),
            go_event_id=_optional_int(row, "goeventid"),
            go_disaster_type_id=_required_int(row, "godisastertypeid"),
            name=_required_string(row, "name", 256),
            glide=_optional_string(row, "glide", 18),
            disaster_start_at=_required_datetime(row, "disasterstartat"),
            description=_optional_string(row, "description", 1000),
            people_affected=_optional_int(row, "peopleaffected", allow_zero=True),
            ifrc_severity_level=severity,
            ifrc_severity_level_updated_at=_optional_datetime(row, "ifrcseveritylevelupdatedat"),
            country_source_ids=_uuid_tuple(row, "country_grc_source_ids", required=True),
            district_source_ids=_uuid_tuple(row, "district_grc_source_ids", required=False),
            is_active=_required_bool(row, "isactive"),
            source_updated_at=_optional_datetime(row, "sourceupdatedat"),
            ingested_at=_optional_datetime(row, "ingestedat"),
        )

    def content_hash(self) -> str:
        content = {
            "description": self.description,
            "disaster_start_at": self.disaster_start_at.isoformat(),
            "glide": self.glide,
            "country_source_ids": sorted(str(value) for value in self.country_source_ids),
            "go_disaster_type_id": self.go_disaster_type_id,
            "district_source_ids": sorted(str(value) for value in self.district_source_ids),
            "ifrc_severity_level": self.ifrc_severity_level,
            "ifrc_severity_level_updated_at": (
                self.ifrc_severity_level_updated_at.isoformat()
                if self.ifrc_severity_level_updated_at
                else None
            ),
            "is_active": self.is_active,
            "name": self.name,
            "people_affected": self.people_affected,
        }
        serialized = json.dumps(content, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def event_defaults(self) -> dict[str, object]:
        return {
            "name": self.name,
            "dtype_id": self.go_disaster_type_id,
            "disaster_start_date": self.disaster_start_at,
            "visibility": VisibilityChoices.MEMBERSHIP,
            "summary": self.description,
            "num_affected": self.people_affected,
            "ifrc_severity_level": self.ifrc_severity_level,
            "ifrc_severity_level_update_date": self.ifrc_severity_level_updated_at,
            "glide": self.glide,
        }


@dataclass(frozen=True)
class GRCEventPublicationResult:
    rows_seen: int
    rows_created: int
    rows_updated: int
    rows_unchanged: int = 0


def _validate_event_batch(records: Sequence[GRCGoldDisasterEvent]) -> None:
    unique_fields = {
        "grc_source_id": [record.source_id for record in records],
        "disastereventkey": [record.disaster_event_key for record in records],
        "goeventid": [record.go_event_id for record in records if record.go_event_id is not None],
    }
    for field, values in unique_fields.items():
        if len(values) != len(set(values)):
            raise GRCEventProjectionError(f"duplicate {field} in dimdisasterevent snapshot")

    inactive_ids = sorted(str(record.source_id) for record in records if not record.is_active)
    if inactive_ids:
        values = ", ".join(str(value) for value in inactive_ids)
        raise GRCEventProjectionError(
            "inactive Events require deletion reconciliation and cannot be published as active rows; "
            f"source GUID(s): {values}"
        )


def publish_grc_event_snapshot(
    records: Sequence[GRCGoldDisasterEvent],
    sync_run: GRCSyncRun,
    source_system: str = "grc_gold",
) -> GRCEventPublicationResult:
    """Publish active Gold disaster Events through the existing GO Event contract."""

    if not source_system.strip():
        raise GRCEventProjectionError("source_system must not be empty")
    if sync_run.pk is None or sync_run.status != GRCSyncRun.Status.RUNNING:
        raise GRCEventProjectionError("sync_run must be a saved running GRC sync run")

    source_system = source_system.strip()
    records = tuple(records)
    _validate_event_batch(records)

    disaster_type_ids = {record.go_disaster_type_id for record in records}
    missing_type_ids = disaster_type_ids - set(DisasterType.objects.in_bulk(disaster_type_ids))
    if missing_type_ids:
        values = ", ".join(str(value) for value in sorted(missing_type_ids))
        raise GRCEventProjectionError(f"DisasterType reference must exist first; missing GO ID(s): {values}")

    country_target_ids = _resolve_target_map(
        source_system=source_system,
        entity_type=GRCEntityType.COUNTRY,
        source_ids=(source_id for record in records for source_id in record.country_source_ids),
        target_model=Country,
    )
    countries = Country.objects.select_related("region").in_bulk(country_target_ids.values())

    countries_without_region = sorted(
        country_id for country_id, country in countries.items() if country.region_id is None
    )
    if countries_without_region:
        values = ", ".join(str(value) for value in countries_without_region)
        raise GRCEventProjectionError(f"Event countries require projected GO Region values: {values}")

    district_target_ids = _resolve_target_map(
        source_system=source_system,
        entity_type=GRCEntityType.DISTRICT,
        source_ids=(source_id for record in records for source_id in record.district_source_ids),
        target_model=District,
    )
    districts = District.objects.in_bulk(district_target_ids.values())

    for record in records:
        outside_country_ids = sorted(
            str(district_source_id)
            for district_source_id in record.district_source_ids
            if districts[district_target_ids[district_source_id]].country_id
            not in {country_target_ids[source_id] for source_id in record.country_source_ids}
        )
        if outside_country_ids:
            values = ", ".join(str(value) for value in outside_country_ids)
            raise GRCEventProjectionError(
                f"Event districts must belong to an Event country; invalid source GUID(s): {values}"
            )

    created_count = 0
    unchanged_count = 0
    with transaction.atomic():
        published_at = timezone.now()
        target_sequence_dirty = True

        for record in records:
            target_id = _resolve_target_id(
                source_system=source_system,
                entity_type=GRCEntityType.DISASTER_EVENT,
                source_id=record.source_id,
                target_model=Event,
                preferred_target_id=record.go_event_id,
            )
            unchanged_count += int(
                grc_source_content_matches(
                    source_system=source_system,
                    entity_type=GRCEntityType.DISASTER_EVENT,
                    source_id=record.source_id,
                    target_model=Event,
                    target_object_id=target_id,
                    content_hash=record.content_hash(),
                    is_deleted=False,
                )
            )
            if target_id is None:
                if target_sequence_dirty:
                    advance_grc_target_sequence(Event)
                    target_sequence_dirty = False
                event = Event.objects.create(**record.event_defaults())
                created = True
            else:
                event, created = Event.objects.update_or_create(
                    pk=target_id,
                    defaults=record.event_defaults(),
                )
                target_sequence_dirty = target_sequence_dirty or created
            created_count += int(created)

            event_countries = [
                countries[country_target_ids[source_id]] for source_id in record.country_source_ids
            ]
            event.countries.set(event_countries)
            event.countries_for_preview.set(event_countries)
            event.regions.set({country.region_id for country in event_countries})
            event.districts.set(
                district_target_ids[source_id] for source_id in record.district_source_ids
            )

            publish_grc_source_record(
                source_system=source_system,
                entity_type=GRCEntityType.DISASTER_EVENT,
                source_id=record.source_id,
                target_model=Event,
                target_object_id=event.pk,
                source_ingested_at=record.ingested_at,
                content_hash=record.content_hash(),
                is_deleted=False,
                sync_run=sync_run,
                published_at=published_at,
            )

    return GRCEventPublicationResult(
        rows_seen=len(records),
        rows_created=created_count,
        rows_updated=len(records) - created_count,
        rows_unchanged=unchanged_count,
    )
