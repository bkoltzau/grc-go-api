import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.contenttypes.models import ContentType
from django.contrib.gis.geos import Point, Polygon
from django.db import transaction
from django.utils import timezone

from api.models import Country, CountryType, Region, RegionName
from grc_read_model.models import GRCEntityType, GRCSourceRecord, GRCSyncRun


class GRCCountryProjectionError(ValueError):
    pass


def _required_string(row: Mapping[str, object], field: str, max_length: int) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise GRCCountryProjectionError(f"{field} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise GRCCountryProjectionError(f"{field} exceeds the GO maximum length of {max_length}")
    return normalized


def _optional_string(row: Mapping[str, object], field: str) -> str | None:
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise GRCCountryProjectionError(f"{field} must be a string or null")
    normalized = value.strip()
    return normalized or None


def _required_int(row: Mapping[str, object], field: str, *, allow_zero: bool = False) -> int:
    value = row.get(field)
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "non-negative" if allow_zero else "positive"
        raise GRCCountryProjectionError(f"{field} must be a {qualifier} integer")
    return value


def _optional_int(row: Mapping[str, object], field: str) -> int | None:
    value = row.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GRCCountryProjectionError(f"{field} must be a positive integer or null")
    return value


def _required_bool(row: Mapping[str, object], field: str) -> bool:
    value = row.get(field)
    if not isinstance(value, bool):
        raise GRCCountryProjectionError(f"{field} must be a boolean")
    return value


def _required_decimal(row: Mapping[str, object], field: str) -> Decimal:
    value = row.get(field)
    if value is None or isinstance(value, bool):
        raise GRCCountryProjectionError(f"{field} must be numeric")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise GRCCountryProjectionError(f"{field} must be numeric") from None


def _optional_datetime(row: Mapping[str, object], field: str) -> datetime | None:
    value = row.get(field)
    if value is not None and not isinstance(value, datetime):
        raise GRCCountryProjectionError(f"{field} must be a datetime or null")
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise GRCCountryProjectionError(f"{field} must be timezone-aware")
    return value


@dataclass(frozen=True)
class GRCGoldCountry:
    country_key: int
    go_country_id: int
    go_region_id: int
    go_region_name_id: int
    go_record_type_id: int
    name: str
    iso2: str
    iso3: str
    region_label: str
    independent: bool
    is_active: bool
    society_name: str | None
    sovereign_country_key: int | None
    centroid_latitude: Decimal
    centroid_longitude: Decimal
    bbox_west: Decimal
    bbox_south: Decimal
    bbox_east: Decimal
    bbox_north: Decimal
    source_updated_at: datetime | None
    ingested_at: datetime | None

    @classmethod
    def from_gold_row(cls, row: Mapping[str, object]) -> "GRCGoldCountry":
        go_record_type_id = _required_int(row, "gorecordtypeid")
        go_region_name_id = _required_int(row, "goregionnameid", allow_zero=True)
        try:
            CountryType(go_record_type_id)
        except ValueError:
            raise GRCCountryProjectionError(f"unsupported gorecordtypeid: {go_record_type_id}") from None
        try:
            RegionName(go_region_name_id)
        except ValueError:
            raise GRCCountryProjectionError(f"unsupported goregionnameid: {go_region_name_id}") from None

        region_label = _required_string(row, "region", 100)

        iso2 = _required_string(row, "iso2", 2)
        iso3 = _required_string(row, "iso3", 3)
        if len(iso2) != 2 or iso2 != iso2.upper():
            raise GRCCountryProjectionError("iso2 must contain exactly two uppercase characters")
        if len(iso3) != 3 or iso3 != iso3.upper():
            raise GRCCountryProjectionError("iso3 must contain exactly three uppercase characters")

        centroid_latitude = _required_decimal(row, "centroidlatitude")
        centroid_longitude = _required_decimal(row, "centroidlongitude")
        bbox_west = _required_decimal(row, "bboxwest")
        bbox_south = _required_decimal(row, "bboxsouth")
        bbox_east = _required_decimal(row, "bboxeast")
        bbox_north = _required_decimal(row, "bboxnorth")

        if not Decimal("-90") <= centroid_latitude <= Decimal("90"):
            raise GRCCountryProjectionError("centroidlatitude must be between -90 and 90")
        if not Decimal("-180") <= centroid_longitude <= Decimal("180"):
            raise GRCCountryProjectionError("centroidlongitude must be between -180 and 180")
        if not Decimal("-180") <= bbox_west < bbox_east <= Decimal("180"):
            raise GRCCountryProjectionError("bbox west/east values are invalid")
        if not Decimal("-90") <= bbox_south < bbox_north <= Decimal("90"):
            raise GRCCountryProjectionError("bbox south/north values are invalid")

        return cls(
            country_key=_required_int(row, "countrykey"),
            go_country_id=_required_int(row, "gocountryid"),
            go_region_id=_required_int(row, "goregionid"),
            go_region_name_id=go_region_name_id,
            go_record_type_id=go_record_type_id,
            name=_required_string(row, "name", 100),
            iso2=iso2,
            iso3=iso3,
            region_label=region_label,
            independent=_required_bool(row, "independentflag"),
            is_active=_required_bool(row, "isactive"),
            society_name=_optional_string(row, "societyname"),
            sovereign_country_key=_optional_int(row, "sovereigncountrykey"),
            centroid_latitude=centroid_latitude,
            centroid_longitude=centroid_longitude,
            bbox_west=bbox_west,
            bbox_south=bbox_south,
            bbox_east=bbox_east,
            bbox_north=bbox_north,
            source_updated_at=_optional_datetime(row, "sourceupdatedat"),
            ingested_at=_optional_datetime(row, "ingestedat"),
        )

    def content_hash(self) -> str:
        content = {
            "bbox": [str(self.bbox_west), str(self.bbox_south), str(self.bbox_east), str(self.bbox_north)],
            "centroid": [str(self.centroid_longitude), str(self.centroid_latitude)],
            "country_key": self.country_key,
            "go_country_id": self.go_country_id,
            "go_region_id": self.go_region_id,
            "go_region_name_id": self.go_region_name_id,
            "go_record_type_id": self.go_record_type_id,
            "independent": self.independent,
            "is_active": self.is_active,
            "iso2": self.iso2,
            "iso3": self.iso3,
            "name": self.name,
            "region_label": self.region_label,
            "society_name": self.society_name,
            "sovereign_country_key": self.sovereign_country_key,
        }
        serialized = json.dumps(content, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def country_defaults(self) -> dict[str, object]:
        centroid = Point(float(self.centroid_longitude), float(self.centroid_latitude), srid=4326)
        bbox = Polygon.from_bbox(
            (float(self.bbox_west), float(self.bbox_south), float(self.bbox_east), float(self.bbox_north))
        )
        bbox.srid = 4326
        return {
            "name": self.name,
            "record_type": self.go_record_type_id,
            "iso": self.iso2,
            "iso3": self.iso3,
            "society_name": self.society_name or "",
            "region_id": self.go_region_id,
            "centroid": centroid,
            "bbox": bbox,
            "independent": self.independent,
            "is_deprecated": not self.is_active,
        }


@dataclass(frozen=True)
class GRCCountryPublicationResult:
    rows_seen: int
    rows_created: int
    rows_updated: int
    rows_deprecated: int


def _validate_country_batch(records: Sequence[GRCGoldCountry]) -> None:
    unique_fields = {
        "countrykey": [record.country_key for record in records],
        "gocountryid": [record.go_country_id for record in records],
        "iso2": [record.iso2 for record in records],
        "iso3": [record.iso3 for record in records],
    }
    for field, values in unique_fields.items():
        if len(values) != len(set(values)):
            raise GRCCountryProjectionError(f"duplicate {field} in Country snapshot")

    country_keys = {record.country_key for record in records}
    unresolved_sovereign_keys = {
        record.sovereign_country_key
        for record in records
        if record.sovereign_country_key is not None and record.sovereign_country_key not in country_keys
    }
    if unresolved_sovereign_keys:
        values = ", ".join(str(value) for value in sorted(unresolved_sovereign_keys))
        raise GRCCountryProjectionError(f"unresolved sovereigncountrykey value(s): {values}")

    region_values: dict[int, tuple[int, str]] = {}
    for record in records:
        region_value = (record.go_region_name_id, record.region_label)
        previous_value = region_values.setdefault(record.go_region_id, region_value)
        if previous_value != region_value:
            raise GRCCountryProjectionError(
                f"goregionid {record.go_region_id} has conflicting GO enum/label values"
            )


def publish_grc_country_snapshot(
    records: Sequence[GRCGoldCountry],
    sync_run: GRCSyncRun,
    source_system: str = "grc_gold",
) -> GRCCountryPublicationResult:
    """Publish one complete dimcountry snapshot into the existing GO tables."""

    if not source_system.strip():
        raise GRCCountryProjectionError("source_system must not be empty")
    if sync_run.pk is None or sync_run.status != GRCSyncRun.Status.RUNNING:
        raise GRCCountryProjectionError("sync_run must be a saved running GRC sync run")

    source_system = source_system.strip()
    records = tuple(records)
    _validate_country_batch(records)

    created_count = 0
    deprecated_count = 0

    with transaction.atomic():
        region_rows = {
            (record.go_region_id, record.go_region_name_id, record.region_label) for record in records
        }
        for go_region_id, go_region_name_id, region_label in sorted(region_rows):
            Region.objects.update_or_create(
                pk=go_region_id,
                defaults={
                    "name": go_region_name_id,
                    "label": region_label,
                },
            )

        countries_by_source_key: dict[int, Country] = {}
        for record in records:
            country, created = Country.objects.update_or_create(
                pk=record.go_country_id,
                defaults=record.country_defaults(),
            )
            countries_by_source_key[record.country_key] = country
            created_count += int(created)
            deprecated_count += int(country.is_deprecated)

        for record in records:
            sovereign_state_id = (
                countries_by_source_key[record.sovereign_country_key].pk
                if record.sovereign_country_key is not None
                else None
            )
            Country.objects.filter(pk=record.go_country_id).update(sovereign_state_id=sovereign_state_id)

        target_content_type = ContentType.objects.get_for_model(Country)
        published_at = timezone.now()
        for record in records:
            GRCSourceRecord.objects.update_or_create(
                source_system=source_system,
                entity_type=GRCEntityType.COUNTRY,
                source_id=str(record.go_country_id),
                defaults={
                    "source_ingested_at": record.ingested_at,
                    "content_hash": record.content_hash(),
                    "is_deleted": False,
                    "target_content_type": target_content_type,
                    "target_object_id": record.go_country_id,
                    "last_successful_run": sync_run,
                    "published_at": published_at,
                },
            )

    return GRCCountryPublicationResult(
        rows_seen=len(records),
        rows_created=created_count,
        rows_updated=len(records) - created_count,
        rows_deprecated=deprecated_count,
    )
