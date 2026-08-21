from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from deployments.models import OperationTypes, ProgrammeTypes, Sector, SectorTag, Statuses


class GRCProjectReferenceError(ValueError):
    pass


def _required_int(row: Mapping[str, object], field: str, *, allow_zero: bool = False) -> int:
    value = row.get(field)
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "non-negative" if allow_zero else "positive"
        raise GRCProjectReferenceError(f"{field} must be a {qualifier} integer")
    return value


def _optional_int(row: Mapping[str, object], field: str, *, allow_zero: bool = False) -> int | None:
    if row.get(field) is None:
        return None
    return _required_int(row, field, allow_zero=allow_zero)


def _required_string(row: Mapping[str, object], field: str, max_length: int) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise GRCProjectReferenceError(f"{field} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise GRCProjectReferenceError(f"{field} exceeds the Gold maximum length of {max_length}")
    return normalized


def _required_date(row: Mapping[str, object], field: str) -> date:
    value = row.get(field)
    if isinstance(value, datetime) or not isinstance(value, date):
        raise GRCProjectReferenceError(f"{field} must be a date")
    return value


def _validate_choice(value: int, field: str, choices: type) -> int:
    if value not in choices.values:
        supported = ", ".join(str(item) for item in choices.values)
        raise GRCProjectReferenceError(f"{field} must be an exact GO value ({supported})")
    return value


@dataclass(frozen=True)
class GRCProjectControlledValues:
    start_date: date
    end_date: date
    programme_type: int
    operation_type: int
    status: int

    @classmethod
    def from_gold_row(cls, row: Mapping[str, object]) -> "GRCProjectControlledValues":
        start_date = _required_date(row, "startdate")
        end_date = _required_date(row, "enddate")
        if end_date < start_date:
            raise GRCProjectReferenceError("enddate must not be before startdate")

        return cls(
            start_date=start_date,
            end_date=end_date,
            programme_type=_validate_choice(
                _required_int(row, "goprojectprogrammetypeid", allow_zero=True),
                "goprojectprogrammetypeid",
                ProgrammeTypes,
            ),
            operation_type=_validate_choice(
                _required_int(row, "goprojectoperationtypeid", allow_zero=True),
                "goprojectoperationtypeid",
                OperationTypes,
            ),
            status=_validate_choice(
                _required_int(row, "goprojectstatusid", allow_zero=True),
                "goprojectstatusid",
                Statuses,
            ),
        )

    def derived_status(self, as_of: date) -> int:
        if isinstance(as_of, datetime) or not isinstance(as_of, date):
            raise GRCProjectReferenceError("as_of must be a date")
        if self.start_date > as_of:
            return Statuses.PLANNED
        if as_of <= self.end_date:
            return Statuses.ONGOING
        return Statuses.COMPLETED

    def project_defaults(self, as_of: date) -> dict[str, object]:
        derived_status = self.derived_status(as_of)
        if self.status != derived_status:
            raise GRCProjectReferenceError(
                "goprojectstatusid conflicts with the status that upstream GO derives from startdate/enddate"
            )
        return {
            "start_date": self.start_date,
            "end_date": self.end_date,
            "programme_type": self.programme_type,
            "operation_type": self.operation_type,
            "status": derived_status,
        }


@dataclass(frozen=True)
class GRCGoldProjectSector:
    sector_key: int
    primary_sector_id: int | None
    secondary_sector_tag_id: int | None
    code: str
    name: str

    @classmethod
    def from_gold_row(cls, row: Mapping[str, object]) -> "GRCGoldProjectSector":
        return cls(
            sector_key=_required_int(row, "sectorkey"),
            primary_sector_id=_optional_int(row, "goprojectprimarysectorid", allow_zero=True),
            secondary_sector_tag_id=_optional_int(
                row,
                "goprojectsecondarysectortagid",
                allow_zero=True,
            ),
            code=_required_string(row, "code", 50),
            name=_required_string(row, "name", 200),
        )


def validate_grc_project_sectors(records: Sequence[GRCGoldProjectSector]) -> None:
    records = tuple(records)
    unique_fields = {
        "sectorkey": [record.sector_key for record in records],
        "goprojectprimarysectorid": [
            record.primary_sector_id
            for record in records
            if record.primary_sector_id is not None
        ],
        "goprojectsecondarysectortagid": [
            record.secondary_sector_tag_id
            for record in records
            if record.secondary_sector_tag_id is not None
        ],
    }
    for field, values in unique_fields.items():
        if len(values) != len(set(values)):
            raise GRCProjectReferenceError(f"duplicate {field} in dimsector snapshot")

    primary_ids = {
        record.primary_sector_id
        for record in records
        if record.primary_sector_id is not None
    }
    missing_primary_ids = primary_ids - set(Sector.objects.in_bulk(primary_ids))
    if missing_primary_ids:
        values = ", ".join(str(value) for value in sorted(missing_primary_ids))
        raise GRCProjectReferenceError(f"missing upstream GO Sector ID(s): {values}")

    secondary_ids = {
        record.secondary_sector_tag_id
        for record in records
        if record.secondary_sector_tag_id is not None
    }
    missing_secondary_ids = secondary_ids - set(SectorTag.objects.in_bulk(secondary_ids))
    if missing_secondary_ids:
        values = ", ".join(str(value) for value in sorted(missing_secondary_ids))
        raise GRCProjectReferenceError(f"missing upstream GO SectorTag ID(s): {values}")
