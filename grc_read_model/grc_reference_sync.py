from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from grc_read_model.grc_country_projection import (
    GRCGoldCountry,
    publish_grc_country_snapshot,
)
from grc_read_model.grc_district_projection import (
    GRCGoldDistrict,
    publish_grc_district_snapshot,
)
from grc_read_model.grc_event_projection import (
    GRCGoldDisasterEvent,
    GRCGoldDisasterType,
    publish_grc_event_snapshot,
    validate_grc_disaster_types,
)
from grc_read_model.models import GRCReadModelState, GRCSyncRun


class GRCReferenceSyncError(ValueError):
    pass


@dataclass(frozen=True)
class GRCGoldReferenceSnapshot:
    countries: Sequence[GRCGoldCountry]
    districts: Sequence[GRCGoldDistrict]
    disaster_types: Sequence[GRCGoldDisasterType]
    events: Sequence[GRCGoldDisasterEvent]
    watermark: datetime

    def __post_init__(self):
        record_types = {
            "countries": GRCGoldCountry,
            "districts": GRCGoldDistrict,
            "disaster_types": GRCGoldDisasterType,
            "events": GRCGoldDisasterEvent,
        }
        for field, expected_type in record_types.items():
            records = tuple(getattr(self, field))
            if any(not isinstance(record, expected_type) for record in records):
                raise GRCReferenceSyncError(f"{field} must contain only {expected_type.__name__} records")
            object.__setattr__(self, field, records)

        if not self.countries:
            raise GRCReferenceSyncError("countries must contain a complete non-empty dimcountry snapshot")
        if not isinstance(self.watermark, datetime):
            raise GRCReferenceSyncError("watermark must be a datetime")
        if self.watermark.tzinfo is None or self.watermark.utcoffset() is None:
            raise GRCReferenceSyncError("watermark must be timezone-aware")


def _validated_name(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GRCReferenceSyncError(f"{field} must not be empty")
    normalized = value.strip()
    if len(normalized) > 128:
        raise GRCReferenceSyncError(f"{field} exceeds the 128-character metadata limit")
    return normalized


def publish_grc_reference_snapshot(
    snapshot: GRCGoldReferenceSnapshot,
    *,
    source_system: str = "grc_gold",
    stream: str = "country_district_event",
    pipeline: str = "grc_reference",
) -> GRCSyncRun:
    """Atomically publish one dependency-ordered Gold reference snapshot."""

    if not isinstance(snapshot, GRCGoldReferenceSnapshot):
        raise GRCReferenceSyncError("snapshot must be a GRCGoldReferenceSnapshot")
    source_system = _validated_name(source_system, "source_system")
    stream = _validated_name(stream, "stream")
    pipeline = _validated_name(pipeline, "pipeline")

    sync_run = GRCSyncRun.objects.create(
        pipeline=pipeline,
        source_watermark_to=snapshot.watermark,
    )
    previous_watermark = None

    try:
        with transaction.atomic():
            state, _ = GRCReadModelState.objects.select_for_update().get_or_create(
                source_system=source_system,
                stream=stream,
            )
            previous_watermark = state.last_successful_watermark
            if previous_watermark is not None and snapshot.watermark < previous_watermark:
                raise GRCReferenceSyncError(
                    "snapshot watermark must not be older than the last successfully published watermark"
                )

            sync_run.source_watermark_from = previous_watermark
            sync_run.save(update_fields=("source_watermark_from",))

            required_disaster_type_ids = {record.go_disaster_type_id for record in snapshot.events}
            validate_grc_disaster_types(
                snapshot.disaster_types,
                required_go_ids=required_disaster_type_ids,
            )
            country_result = publish_grc_country_snapshot(
                snapshot.countries,
                sync_run,
                source_system=source_system,
            )
            district_result = publish_grc_district_snapshot(
                snapshot.districts,
                sync_run,
                source_system=source_system,
            )
            event_result = publish_grc_event_snapshot(
                snapshot.events,
                sync_run,
                source_system=source_system,
            )

            details = {
                "countries": asdict(country_result),
                "districts": asdict(district_result),
                "disaster_types": {"rows_seen": len(snapshot.disaster_types)},
                "events": asdict(event_result),
            }
            sync_run.status = GRCSyncRun.Status.SUCCEEDED
            sync_run.completed_at = timezone.now()
            sync_run.rows_seen = (
                country_result.rows_seen
                + district_result.rows_seen
                + len(snapshot.disaster_types)
                + event_result.rows_seen
            )
            sync_run.rows_published = (
                country_result.rows_seen + district_result.rows_seen + event_result.rows_seen
            )
            sync_run.rows_deleted = 0
            sync_run.error_message = ""
            sync_run.details = details
            sync_run.save(
                update_fields=(
                    "status",
                    "completed_at",
                    "source_watermark_from",
                    "rows_seen",
                    "rows_published",
                    "rows_deleted",
                    "error_message",
                    "details",
                )
            )

            state.last_successful_watermark = snapshot.watermark
            state.last_successful_run = sync_run
            state.save(
                update_fields=(
                    "last_successful_watermark",
                    "last_successful_run",
                    "updated_at",
                )
            )
    except Exception as exc:
        GRCSyncRun.objects.filter(pk=sync_run.pk).update(
            status=GRCSyncRun.Status.FAILED,
            completed_at=timezone.now(),
            source_watermark_from=previous_watermark,
            source_watermark_to=snapshot.watermark,
            error_message=str(exc),
            details={"error_type": type(exc).__name__},
        )
        raise

    sync_run.refresh_from_db()
    return sync_run
