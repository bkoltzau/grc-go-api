import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.contrib.gis.geos import Point
from django.db import transaction
from django.utils import timezone

from api.models import Country, District
from grc_read_model.grc_identity import (
    GRCIdentityError,
    grc_source_content_matches,
    parse_grc_source_id,
    publish_grc_source_record,
    resolve_grc_target_id as _resolve_grc_target_id,
    resolve_grc_target_map as _resolve_grc_target_map,
)
from grc_read_model.models import GRCEntityType, GRCSyncRun


class GRCDistrictProjectionError(ValueError):
    pass


def _resolve_target_id(**kwargs) -> int | None:
    try:
        return _resolve_grc_target_id(**kwargs)
    except GRCIdentityError as exc:
        raise GRCDistrictProjectionError(str(exc)) from exc


def _resolve_target_map(**kwargs) -> dict[UUID, int]:
    try:
        return _resolve_grc_target_map(**kwargs)
    except GRCIdentityError as exc:
        raise GRCDistrictProjectionError(str(exc)) from exc


def _required_string(row: Mapping[str, object], field: str, max_length: int) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise GRCDistrictProjectionError(f"{field} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise GRCDistrictProjectionError(f"{field} exceeds the GO maximum length of {max_length}")
    return normalized


def _required_int(row: Mapping[str, object], field: str, *, allow_zero: bool = False) -> int:
    value = row.get(field)
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "non-negative" if allow_zero else "positive"
        raise GRCDistrictProjectionError(f"{field} must be a {qualifier} integer")
    return value


def _optional_int(row: Mapping[str, object], field: str) -> int | None:
    value = row.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GRCDistrictProjectionError(f"{field} must be a positive integer or null")
    return value


def _required_bool(row: Mapping[str, object], field: str) -> bool:
    value = row.get(field)
    if not isinstance(value, bool):
        raise GRCDistrictProjectionError(f"{field} must be a boolean")
    return value


def _required_decimal(row: Mapping[str, object], field: str) -> Decimal:
    value = row.get(field)
    if value is None or isinstance(value, bool):
        raise GRCDistrictProjectionError(f"{field} must be numeric")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise GRCDistrictProjectionError(f"{field} must be numeric") from None


def _optional_datetime(row: Mapping[str, object], field: str) -> datetime | None:
    value = row.get(field)
    if value is not None and not isinstance(value, datetime):
        raise GRCDistrictProjectionError(f"{field} must be a datetime or null")
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise GRCDistrictProjectionError(f"{field} must be timezone-aware")
    return value


@dataclass(frozen=True)
class GRCGoldDistrict:
    source_id: UUID
    location_key: int
    go_district_id: int | None
    country_source_id: UUID
    country_key: int
    admin_level: int
    name: str
    code: str
    latitude: Decimal
    longitude: Decimal
    is_active: bool
    source_updated_at: datetime | None
    ingested_at: datetime | None

    @classmethod
    def from_gold_row(cls, row: Mapping[str, object]) -> "GRCGoldDistrict":
        admin_level = _required_int(row, "adminlevel", allow_zero=True)
        if admin_level != 1:
            raise GRCDistrictProjectionError(
                f"adminlevel {admin_level} cannot be published as GO District; only confirmed ADM1 rows are supported"
            )

        latitude = _required_decimal(row, "latitude")
        longitude = _required_decimal(row, "longitude")
        if not Decimal("-90") <= latitude <= Decimal("90"):
            raise GRCDistrictProjectionError("latitude must be between -90 and 90")
        if not Decimal("-180") <= longitude <= Decimal("180"):
            raise GRCDistrictProjectionError("longitude must be between -180 and 180")

        return cls(
            source_id=parse_grc_source_id(row.get("grc_source_id")),
            location_key=_required_int(row, "locationkey"),
            go_district_id=_optional_int(row, "godistrictid"),
            country_source_id=parse_grc_source_id(
                row.get("country_grc_source_id"),
                "country_grc_source_id",
            ),
            country_key=_required_int(row, "countrykey"),
            admin_level=admin_level,
            name=_required_string(row, "name", 100),
            code=_required_string(row, "pcode", 10),
            latitude=latitude,
            longitude=longitude,
            is_active=_required_bool(row, "isactive"),
            source_updated_at=_optional_datetime(row, "sourceupdatedat"),
            ingested_at=_optional_datetime(row, "ingestedat"),
        )

    def content_hash(self) -> str:
        content = {
            "admin_level": self.admin_level,
            "code": self.code,
            "country_source_id": str(self.country_source_id),
            "is_active": self.is_active,
            "latitude": str(self.latitude),
            "longitude": str(self.longitude),
            "name": self.name,
        }
        serialized = json.dumps(content, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def district_defaults(self, country_id: int) -> dict[str, object]:
        return {
            "name": self.name,
            "code": self.code,
            "country_id": country_id,
            "centroid": Point(float(self.longitude), float(self.latitude), srid=4326),
            "is_deprecated": not self.is_active,
        }


@dataclass(frozen=True)
class GRCDistrictPublicationResult:
    rows_seen: int
    rows_created: int
    rows_updated: int
    rows_deprecated: int
    rows_unchanged: int = 0


def _validate_district_batch(records: Sequence[GRCGoldDistrict]) -> None:
    unique_fields = {
        "grc_source_id": [record.source_id for record in records],
        "locationkey": [record.location_key for record in records],
        "godistrictid": [record.go_district_id for record in records if record.go_district_id is not None],
    }
    for field, values in unique_fields.items():
        if len(values) != len(set(values)):
            raise GRCDistrictProjectionError(f"duplicate {field} in ADM1 location snapshot")

    country_targets: dict[int, UUID] = {}
    for record in records:
        existing_target = country_targets.setdefault(record.country_key, record.country_source_id)
        if existing_target != record.country_source_id:
            raise GRCDistrictProjectionError(
                f"countrykey {record.country_key} maps to conflicting grc_source_id values"
            )


def publish_grc_district_snapshot(
    records: Sequence[GRCGoldDistrict],
    sync_run: GRCSyncRun,
    source_system: str = "grc_gold",
) -> GRCDistrictPublicationResult:
    """Publish mapped ADM1 dimlocation rows into the existing GO District table."""

    if not source_system.strip():
        raise GRCDistrictProjectionError("source_system must not be empty")
    if sync_run.pk is None or sync_run.status != GRCSyncRun.Status.RUNNING:
        raise GRCDistrictProjectionError("sync_run must be a saved running GRC sync run")

    source_system = source_system.strip()
    records = tuple(records)
    _validate_district_batch(records)

    country_target_ids = _resolve_target_map(
        source_system=source_system,
        entity_type=GRCEntityType.COUNTRY,
        source_ids=(record.country_source_id for record in records),
        target_model=Country,
    )

    created_count = 0
    deprecated_count = 0
    unchanged_count = 0

    with transaction.atomic():
        published_at = timezone.now()

        for record in records:
            target_id = _resolve_target_id(
                source_system=source_system,
                entity_type=GRCEntityType.DISTRICT,
                source_id=record.source_id,
                target_model=District,
                preferred_target_id=record.go_district_id,
            )
            unchanged_count += int(
                grc_source_content_matches(
                    source_system=source_system,
                    entity_type=GRCEntityType.DISTRICT,
                    source_id=record.source_id,
                    target_model=District,
                    target_object_id=target_id,
                    content_hash=record.content_hash(),
                    is_deleted=False,
                )
            )
            defaults = record.district_defaults(country_target_ids[record.country_source_id])
            if target_id is None:
                district = District.objects.create(**defaults)
                created = True
            else:
                district, created = District.objects.update_or_create(
                    pk=target_id,
                    defaults=defaults,
                )
            created_count += int(created)
            deprecated_count += int(district.is_deprecated)

            publish_grc_source_record(
                source_system=source_system,
                entity_type=GRCEntityType.DISTRICT,
                source_id=record.source_id,
                target_model=District,
                target_object_id=district.pk,
                source_ingested_at=record.ingested_at,
                content_hash=record.content_hash(),
                is_deleted=False,
                sync_run=sync_run,
                published_at=published_at,
            )

    return GRCDistrictPublicationResult(
        rows_seen=len(records),
        rows_created=created_count,
        rows_updated=len(records) - created_count,
        rows_deprecated=deprecated_count,
        rows_unchanged=unchanged_count,
    )
