from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from grc_read_model.grc_project_projection import (
    GRCGoldProject,
    GRCGoldProjectDeletion,
    publish_grc_project_records,
)
from grc_read_model.grc_project_reference import GRCGoldProjectSector, validate_grc_project_sectors
from grc_read_model.models import GRCReadModelState, GRCSyncRun


class GRCProjectSyncError(ValueError):
    pass


@dataclass(frozen=True)
class GRCGoldProjectSnapshot:
    sectors: Sequence[GRCGoldProjectSector]
    projects: Sequence[GRCGoldProject]
    deletions: Sequence[GRCGoldProjectDeletion]
    watermark: datetime

    def __post_init__(self):
        record_types = {
            "sectors": GRCGoldProjectSector,
            "projects": GRCGoldProject,
            "deletions": GRCGoldProjectDeletion,
        }
        for field, expected_type in record_types.items():
            records = tuple(getattr(self, field))
            if any(not isinstance(record, expected_type) for record in records):
                raise GRCProjectSyncError(f"{field} must contain only {expected_type.__name__} records")
            object.__setattr__(self, field, records)

        if not isinstance(self.watermark, datetime):
            raise GRCProjectSyncError("watermark must be a datetime")
        if self.watermark.tzinfo is None or self.watermark.utcoffset() is None:
            raise GRCProjectSyncError("watermark must be timezone-aware")


def _validated_name(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GRCProjectSyncError(f"{field} must not be empty")
    normalized = value.strip()
    if len(normalized) > 128:
        raise GRCProjectSyncError(f"{field} exceeds the 128-character metadata limit")
    return normalized


def publish_grc_project_snapshot(
    snapshot: GRCGoldProjectSnapshot,
    *,
    source_system: str = "grc_gold",
    stream: str = "project",
    pipeline: str = "grc_project",
) -> GRCSyncRun:
    """Atomically publish one complete Gold Project snapshot and its tombstones."""

    if not isinstance(snapshot, GRCGoldProjectSnapshot):
        raise GRCProjectSyncError("snapshot must be a GRCGoldProjectSnapshot")
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
                raise GRCProjectSyncError(
                    "snapshot watermark must not be older than the last successfully published watermark"
                )

            sync_run.source_watermark_from = previous_watermark
            sync_run.save(update_fields=("source_watermark_from",))

            validate_grc_project_sectors(snapshot.sectors)
            publication_result = publish_grc_project_records(
                snapshot.projects,
                snapshot.deletions,
                sync_run,
                as_of=timezone.now().date(),
                source_system=source_system,
            )

            sync_run.status = GRCSyncRun.Status.SUCCEEDED
            sync_run.completed_at = timezone.now()
            sync_run.rows_seen = publication_result.rows_seen + len(snapshot.sectors)
            sync_run.rows_published = len(snapshot.projects)
            sync_run.rows_deleted = publication_result.rows_deleted
            sync_run.error_message = ""
            sync_run.details = {
                "project_sectors": {"rows_seen": len(snapshot.sectors)},
                "projects": asdict(publication_result),
            }
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
