from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from django.contrib.contenttypes.models import ContentType
from django.db import connection, models
from django.db.models import Max
from django.utils import timezone

from grc_read_model.models import GRCSourceRecord, GRCSyncRun


class GRCIdentityError(ValueError):
    pass


def advance_grc_target_sequence(target_model: type[models.Model]) -> None:
    """Advance, but never rewind, a GO PostgreSQL integer-ID sequence."""

    target_content_type = ContentType.objects.get_for_model(target_model)
    mapped_max = (
        GRCSourceRecord.objects.filter(target_content_type=target_content_type).aggregate(
            value=Max("target_object_id")
        )["value"]
        or 0
    )
    table_name = connection.ops.quote_name(target_model._meta.db_table)
    pk_column = connection.ops.quote_name(target_model._meta.pk.column)

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_serial_sequence(%s, %s)",
            [target_model._meta.db_table, target_model._meta.pk.column],
        )
        sequence_name = cursor.fetchone()[0]
        if sequence_name is None:
            raise GRCIdentityError(f"GO {target_model.__name__} does not have an integer-ID sequence")
        cursor.execute(
            f"SELECT setval(%s::regclass, "
            f"GREATEST(nextval(%s::regclass), COALESCE(MAX({pk_column}), 0), %s), TRUE) "
            f"FROM {table_name}",
            [sequence_name, sequence_name, mapped_max],
        )


def parse_grc_source_id(value: object, field: str = "grc_source_id") -> UUID:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str) or not value.strip():
        raise GRCIdentityError(f"{field} must be a UUID")
    try:
        return UUID(value.strip())
    except ValueError:
        raise GRCIdentityError(f"{field} must be a UUID") from None


def resolve_grc_target_id(
    *,
    source_system: str,
    entity_type: str,
    source_id: UUID,
    target_model: type[models.Model],
    preferred_target_id: int | None,
) -> int | None:
    """Resolve an existing GUID mapping or an approved original GO target ID."""

    source_value = str(source_id)
    target_content_type = ContentType.objects.get_for_model(target_model)
    source_record = GRCSourceRecord.objects.filter(
        source_system=source_system,
        entity_type=entity_type,
        source_id=source_value,
    ).first()
    if source_record is not None:
        if source_record.target_content_type_id != target_content_type.pk:
            raise GRCIdentityError(
                f"{entity_type} {source_value} is mapped to an incompatible GO model"
            )
        if preferred_target_id is not None and preferred_target_id != source_record.target_object_id:
            raise GRCIdentityError(
                f"{entity_type} {source_value} changed its preferred GO ID from "
                f"{source_record.target_object_id} to {preferred_target_id}"
            )
        return source_record.target_object_id

    if preferred_target_id is None:
        return None
    if (
        isinstance(preferred_target_id, bool)
        or not isinstance(preferred_target_id, int)
        or preferred_target_id <= 0
    ):
        raise GRCIdentityError("preferred_target_id must be a positive integer or null")

    collision = GRCSourceRecord.objects.filter(
        source_system=source_system,
        entity_type=entity_type,
        target_content_type=target_content_type,
        target_object_id=preferred_target_id,
    ).first()
    if collision is not None:
        raise GRCIdentityError(
            f"GO {target_model.__name__} ID {preferred_target_id} is already mapped to "
            f"{entity_type} {collision.source_id}"
        )
    return preferred_target_id


def resolve_grc_target_map(
    *,
    source_system: str,
    entity_type: str,
    source_ids: Iterable[UUID],
    target_model: type[models.Model],
) -> dict[UUID, int]:
    """Resolve required related Gold GUIDs to existing GO cache rows."""

    normalized_ids = set(source_ids)
    if not normalized_ids:
        return {}
    source_values = {str(source_id) for source_id in normalized_ids}
    target_content_type = ContentType.objects.get_for_model(target_model)
    source_records = GRCSourceRecord.objects.filter(
        source_system=source_system,
        entity_type=entity_type,
        source_id__in=source_values,
    )

    target_map: dict[UUID, int] = {}
    for source_record in source_records:
        source_id = parse_grc_source_id(source_record.source_id, "stored source_id")
        if source_record.target_content_type_id != target_content_type.pk:
            raise GRCIdentityError(
                f"{entity_type} {source_id} is mapped to an incompatible GO model"
            )
        if source_record.is_deleted:
            raise GRCIdentityError(f"{entity_type} {source_id} is marked deleted")
        target_map[source_id] = source_record.target_object_id

    missing_source_ids = normalized_ids - set(target_map)
    if missing_source_ids:
        values = ", ".join(str(value) for value in sorted(missing_source_ids, key=str))
        raise GRCIdentityError(f"missing published {entity_type} source GUID(s): {values}")

    targets = target_model.objects.in_bulk(target_map.values())
    missing_target_ids = set(target_map.values()) - set(targets)
    if missing_target_ids:
        values = ", ".join(str(value) for value in sorted(missing_target_ids))
        raise GRCIdentityError(f"missing mapped GO {target_model.__name__} ID(s): {values}")
    return target_map


def grc_source_content_matches(
    *,
    source_system: str,
    entity_type: str,
    source_id: UUID,
    target_model: type[models.Model],
    target_object_id: int | None,
    content_hash: str,
    is_deleted: bool,
) -> bool:
    """Compare one complete-snapshot row with its last published metadata."""

    if target_object_id is None:
        return False
    target_content_type = ContentType.objects.get_for_model(target_model)
    return GRCSourceRecord.objects.filter(
        source_system=source_system,
        entity_type=entity_type,
        source_id=str(source_id),
        target_content_type=target_content_type,
        target_object_id=target_object_id,
        content_hash=content_hash,
        is_deleted=is_deleted,
    ).exists()


def publish_grc_source_record(
    *,
    source_system: str,
    entity_type: str,
    source_id: UUID,
    target_model: type[models.Model],
    target_object_id: int,
    source_ingested_at: datetime | None,
    content_hash: str,
    is_deleted: bool,
    sync_run: GRCSyncRun,
    published_at: datetime | None = None,
) -> GRCSourceRecord:
    """Persist one canonical GUID-to-GO mapping and reject target collisions."""

    target_content_type = ContentType.objects.get_for_model(target_model)
    source_value = str(source_id)
    collision = GRCSourceRecord.objects.filter(
        source_system=source_system,
        entity_type=entity_type,
        target_content_type=target_content_type,
        target_object_id=target_object_id,
    ).exclude(source_id=source_value).first()
    if collision is not None:
        raise GRCIdentityError(
            f"GO {target_model.__name__} ID {target_object_id} is already mapped to "
            f"{entity_type} {collision.source_id}"
        )

    source_record, _ = GRCSourceRecord.objects.update_or_create(
        source_system=source_system,
        entity_type=entity_type,
        source_id=source_value,
        defaults={
            "source_ingested_at": source_ingested_at,
            "content_hash": content_hash,
            "is_deleted": is_deleted,
            "target_content_type": target_content_type,
            "target_object_id": target_object_id,
            "last_successful_run": sync_run,
            "published_at": published_at or timezone.now(),
        },
    )
    return source_record
