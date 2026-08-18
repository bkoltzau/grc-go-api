import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.contenttypes.models import ContentType
from django.contrib.gis.geos import Point
from django.db import transaction
from django.utils import timezone

from api.models import Country, District
from grc_read_model.models import GRCEntityType, GRCSourceRecord, GRCSyncRun


class GRCDistrictProjectionError(ValueError):
    pass


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
    location_key: int
    go_district_id: int
    go_country_id: int
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
            location_key=_required_int(row, "locationkey"),
            go_district_id=_required_int(row, "godistrictid"),
            go_country_id=_required_int(row, "gocountryid"),
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
            "go_country_id": self.go_country_id,
            "go_district_id": self.go_district_id,
            "is_active": self.is_active,
            "latitude": str(self.latitude),
            "longitude": str(self.longitude),
            "name": self.name,
        }
        serialized = json.dumps(content, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def district_defaults(self) -> dict[str, object]:
        return {
            "name": self.name,
            "code": self.code,
            "country_id": self.go_country_id,
            "centroid": Point(float(self.longitude), float(self.latitude), srid=4326),
            "is_deprecated": not self.is_active,
        }


@dataclass(frozen=True)
class GRCDistrictPublicationResult:
    rows_seen: int
    rows_created: int
    rows_updated: int
    rows_deprecated: int


def _validate_district_batch(records: Sequence[GRCGoldDistrict]) -> None:
    unique_fields = {
        "locationkey": [record.location_key for record in records],
        "godistrictid": [record.go_district_id for record in records],
    }
    for field, values in unique_fields.items():
        if len(values) != len(set(values)):
            raise GRCDistrictProjectionError(f"duplicate {field} in ADM1 location snapshot")

    country_targets: dict[int, int] = {}
    for record in records:
        existing_target = country_targets.setdefault(record.country_key, record.go_country_id)
        if existing_target != record.go_country_id:
            raise GRCDistrictProjectionError(
                f"countrykey {record.country_key} maps to conflicting gocountryid values"
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

    country_ids = {record.go_country_id for record in records}
    countries = Country.objects.in_bulk(country_ids)
    missing_country_ids = country_ids - set(countries)
    if missing_country_ids:
        values = ", ".join(str(value) for value in sorted(missing_country_ids))
        raise GRCDistrictProjectionError(f"Country projection must run first; missing gocountryid value(s): {values}")

    created_count = 0
    deprecated_count = 0

    with transaction.atomic():
        target_content_type = ContentType.objects.get_for_model(District)
        published_at = timezone.now()

        for record in records:
            district, created = District.objects.update_or_create(
                pk=record.go_district_id,
                defaults=record.district_defaults(),
            )
            created_count += int(created)
            deprecated_count += int(district.is_deprecated)

            GRCSourceRecord.objects.update_or_create(
                source_system=source_system,
                entity_type=GRCEntityType.DISTRICT,
                source_id=str(record.go_district_id),
                defaults={
                    "source_ingested_at": record.ingested_at,
                    "content_hash": record.content_hash(),
                    "is_deleted": False,
                    "target_content_type": target_content_type,
                    "target_object_id": record.go_district_id,
                    "last_successful_run": sync_run,
                    "published_at": published_at,
                },
            )

    return GRCDistrictPublicationResult(
        rows_seen=len(records),
        rows_created=created_count,
        rows_updated=len(records) - created_count,
        rows_deprecated=deprecated_count,
    )
