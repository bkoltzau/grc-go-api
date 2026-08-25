from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import psycopg2
from psycopg2 import extensions

from grc_read_model.grc_dwh_reader import GRCDWHConfigurationError, GRCDWHReadError, GRCDWHSettings


class GRCDWHContractError(ValueError):
    pass


@dataclass(frozen=True)
class GRCGoldColumnRequirement:
    scope: str
    table: str
    column: str
    data_types: tuple[str, ...]
    minimum_character_length: int | None = None

    @property
    def identity(self) -> str:
        return f"{self.table}.{self.column}"

    @property
    def expected_type(self) -> str:
        expected = " or ".join(self.data_types)
        if self.minimum_character_length is not None:
            expected = f"{expected}({self.minimum_character_length}+)"
        return expected


def _column(
    scope: str,
    table: str,
    column: str,
    *data_types: str,
    minimum_character_length: int | None = None,
) -> GRCGoldColumnRequirement:
    return GRCGoldColumnRequirement(
        scope=scope,
        table=table,
        column=column,
        data_types=tuple(data_types),
        minimum_character_length=minimum_character_length,
    )


GRC_GOLD_CONTRACT_REQUIREMENTS = (
    # Reference snapshot.
    _column("reference", "dimcountry", "grc_source_id", "uuid"),
    _column("reference", "dimcountry", "countrykey", "integer"),
    _column("reference", "dimcountry", "gocountryid", "integer"),
    _column("reference", "dimcountry", "goregionid", "integer"),
    _column("reference", "dimcountry", "goregionnameid", "smallint"),
    _column("reference", "dimcountry", "gorecordtypeid", "smallint"),
    _column("reference", "dimcountry", "name", "character varying"),
    _column("reference", "dimcountry", "iso2", "character", "character varying"),
    _column("reference", "dimcountry", "iso3", "character", "character varying"),
    _column("reference", "dimcountry", "region", "character varying"),
    _column("reference", "dimcountry", "independentflag", "boolean"),
    _column("reference", "dimcountry", "isactive", "boolean"),
    _column(
        "reference",
        "dimcountry",
        "societyname",
        "character varying",
        minimum_character_length=300,
    ),
    _column("reference", "dimcountry", "sovereigncountrykey", "integer"),
    _column("reference", "dimcountry", "centroidlatitude", "numeric"),
    _column("reference", "dimcountry", "centroidlongitude", "numeric"),
    _column("reference", "dimcountry", "bboxwest", "numeric"),
    _column("reference", "dimcountry", "bboxsouth", "numeric"),
    _column("reference", "dimcountry", "bboxeast", "numeric"),
    _column("reference", "dimcountry", "bboxnorth", "numeric"),
    _column("reference", "dimcountry", "sourceupdatedat", "timestamp with time zone"),
    _column("reference", "dimcountry", "ingestedat", "timestamp with time zone"),
    _column("reference", "dimlocation", "locationkey", "integer"),
    _column("reference", "dimlocation", "grc_source_id", "uuid"),
    _column("reference", "dimlocation", "godistrictid", "integer"),
    _column("reference", "dimlocation", "countrykey", "integer"),
    _column("reference", "dimlocation", "adminlevel", "smallint"),
    _column("reference", "dimlocation", "pcode", "character varying"),
    _column("reference", "dimlocation", "name", "character varying"),
    _column("reference", "dimlocation", "latitude", "numeric"),
    _column("reference", "dimlocation", "longitude", "numeric"),
    _column("reference", "dimlocation", "isactive", "boolean"),
    _column("reference", "dimlocation", "sourceupdatedat", "timestamp with time zone"),
    _column("reference", "dimlocation", "ingestedat", "timestamp with time zone"),
    _column("reference", "dimdisastertype", "disastertypekey", "integer"),
    _column("reference", "dimdisastertype", "godisastertypeid", "integer"),
    _column("reference", "dimdisastertype", "code", "character varying"),
    _column("reference", "dimdisastertype", "name", "character varying"),
    _column("reference", "dimdisastertype", "isactive", "boolean"),
    _column("reference", "dimdisasterevent", "disastereventkey", "integer"),
    _column("reference", "dimdisasterevent", "grc_source_id", "uuid"),
    _column("reference", "dimdisasterevent", "goeventid", "integer"),
    _column("reference", "dimdisasterevent", "disastertypekey", "integer"),
    _column("reference", "dimdisasterevent", "primarycountrykey", "integer"),
    _column("reference", "dimdisasterevent", "name", "character varying"),
    _column("reference", "dimdisasterevent", "glide", "character varying"),
    _column("reference", "dimdisasterevent", "disasterstartat", "timestamp with time zone"),
    _column("reference", "dimdisasterevent", "description", "character varying"),
    _column("reference", "dimdisasterevent", "peopleaffected", "integer"),
    _column("reference", "dimdisasterevent", "goifrcseveritylevelid", "smallint"),
    _column(
        "reference",
        "dimdisasterevent",
        "ifrcseveritylevelupdatedat",
        "timestamp with time zone",
    ),
    _column("reference", "dimdisasterevent", "isactive", "boolean"),
    _column("reference", "dimdisasterevent", "sourceupdatedat", "timestamp with time zone"),
    _column("reference", "dimdisasterevent", "ingestedat", "timestamp with time zone"),
    _column("reference", "bridgedisastereventcountry", "disastereventkey", "integer"),
    _column("reference", "bridgedisastereventcountry", "countrykey", "integer"),
    _column("reference", "bridgedisastereventcountry", "isprimary", "boolean"),
    _column("reference", "bridgedisastereventlocation", "disastereventkey", "integer"),
    _column("reference", "bridgedisastereventlocation", "locationkey", "integer"),
    # Project snapshot and every directly queried dependency.
    _column("project", "factproject", "projectid", "integer"),
    _column("project", "factproject", "grc_source_id", "uuid"),
    _column("project", "factproject", "goprojectid", "integer"),
    _column(
        "project",
        "factproject",
        "projectname",
        "character varying",
        minimum_character_length=500,
    ),
    _column("project", "factproject", "startdatekey", "integer"),
    _column("project", "factproject", "enddatekey", "integer"),
    _column("project", "factproject", "countrykey", "integer"),
    _column("project", "factproject", "organizationkey", "integer"),
    _column("project", "factproject", "sectorkey", "integer"),
    _column("project", "factproject", "operationstatuskey", "integer"),
    _column("project", "factproject", "goprojectprogrammetypeid", "smallint"),
    _column("project", "factproject", "goprojectoperationtypeid", "smallint"),
    _column("project", "factproject", "budgetamountchf", "numeric"),
    _column("project", "factproject", "peopletargeted", "integer"),
    _column("project", "factproject", "peoplereached", "integer"),
    _column("project", "factproject", "ingestedat", "timestamp with time zone"),
    _column("project", "factproject", "isdeleted", "boolean"),
    _column("project", "dimdate", "datekey", "integer"),
    _column("project", "dimdate", "date", "date"),
    _column("project", "dimcountry", "countrykey", "integer"),
    _column("project", "dimcountry", "grc_source_id", "uuid"),
    _column("project", "dimorganization", "organizationkey", "integer"),
    _column("project", "dimorganization", "countrykey", "integer"),
    _column("project", "dimsector", "sectorkey", "integer"),
    _column("project", "dimsector", "code", "character varying"),
    _column("project", "dimsector", "name", "character varying"),
    _column("project", "dimsector", "goprojectprimarysectorid", "integer"),
    _column("project", "dimsector", "goprojectsecondarysectortagid", "integer"),
    _column("project", "dimoperationstatus", "operationstatuskey", "integer"),
    _column("project", "dimoperationstatus", "goprojectstatusid", "smallint"),
    _column("project", "dimlocation", "locationkey", "integer"),
    _column("project", "dimlocation", "grc_source_id", "uuid"),
    _column("project", "dimlocation", "adminlevel", "smallint"),
    _column("project", "bridgeprojectsector", "projectid", "integer"),
    _column("project", "bridgeprojectsector", "sectorkey", "integer"),
    _column("project", "bridgeprojectlocation", "projectid", "integer"),
    _column("project", "bridgeprojectlocation", "locationkey", "integer"),
    _column("project", "bridgeprojectoperation", "projectid", "integer"),
    _column("project", "bridgeprojectoperation", "operationid", "integer"),
    _column("project", "bridgeprojectoperation", "isprimary", "boolean"),
    _column("project", "factoperation", "operationid", "integer"),
    _column("project", "factoperation", "disastereventkey", "integer"),
    _column("project", "factoperation", "disastertypekey", "integer"),
    _column("project", "factoperation", "isdeleted", "boolean"),
    _column("project", "dimdisasterevent", "disastereventkey", "integer"),
    _column("project", "dimdisasterevent", "grc_source_id", "uuid"),
    _column("project", "dimdisastertype", "disastertypekey", "integer"),
    _column("project", "dimdisastertype", "godisastertypeid", "integer"),
)


@dataclass(frozen=True)
class GRCGoldColumnTypeMismatch:
    identity: str
    expected: str
    actual: str


@dataclass(frozen=True)
class GRCGoldContractReport:
    scopes: tuple[str, ...]
    missing_tables: tuple[str, ...]
    missing_columns: tuple[str, ...]
    incompatible_columns: tuple[GRCGoldColumnTypeMismatch, ...]

    @property
    def is_compatible(self) -> bool:
        return not (self.missing_tables or self.missing_columns or self.incompatible_columns)

    def error_messages(self) -> tuple[str, ...]:
        messages = [f"missing table: {table}" for table in self.missing_tables]
        messages.extend(f"missing column: {column}" for column in self.missing_columns)
        messages.extend(
            f"incompatible column: {mismatch.identity} expected {mismatch.expected}, got {mismatch.actual}"
            for mismatch in self.incompatible_columns
        )
        return tuple(messages)

    def raise_for_errors(self) -> None:
        if not self.is_compatible:
            raise GRCDWHContractError("; ".join(self.error_messages()))


_AVAILABLE_SCOPES = frozenset(requirement.scope for requirement in GRC_GOLD_CONTRACT_REQUIREMENTS)
_SCHEMA_QUERY = """
    SELECT
        tables.table_name,
        columns.column_name,
        columns.data_type,
        columns.character_maximum_length
    FROM information_schema.tables AS tables
    LEFT JOIN information_schema.columns AS columns
      ON columns.table_schema = tables.table_schema
     AND columns.table_name = tables.table_name
    WHERE tables.table_schema = %s
      AND tables.table_type IN ('BASE TABLE', 'VIEW')
    ORDER BY tables.table_name, columns.ordinal_position
"""


def _normalize_scopes(scopes: Sequence[str]) -> tuple[str, ...]:
    if isinstance(scopes, (str, bytes)):
        raise GRCDWHContractError("scopes must be a sequence of scope names")
    values = []
    for scope in scopes:
        if not isinstance(scope, str):
            raise GRCDWHContractError("each Gold contract scope must be a string")
        normalized_scope = scope.strip().lower()
        if normalized_scope:
            values.append(normalized_scope)
    normalized = tuple(dict.fromkeys(values))
    if not normalized:
        raise GRCDWHContractError("at least one Gold contract scope is required")
    unknown = sorted(set(normalized) - _AVAILABLE_SCOPES)
    if unknown:
        raise GRCDWHContractError(f"unknown Gold contract scope(s): {', '.join(unknown)}")
    return normalized


def _required_columns(scopes: tuple[str, ...]) -> tuple[GRCGoldColumnRequirement, ...]:
    requirements: dict[tuple[str, str], GRCGoldColumnRequirement] = {}
    for requirement in GRC_GOLD_CONTRACT_REQUIREMENTS:
        if requirement.scope not in scopes:
            continue
        identity = (requirement.table, requirement.column)
        existing = requirements.get(identity)
        if existing is not None and (
            existing.data_types != requirement.data_types
            or existing.minimum_character_length != requirement.minimum_character_length
        ):
            raise GRCDWHContractError(f"conflicting internal requirement for {requirement.identity}")
        requirements[identity] = requirement
    return tuple(requirements[identity] for identity in sorted(requirements))


def _build_report(
    scopes: tuple[str, ...],
    requirements: tuple[GRCGoldColumnRequirement, ...],
    rows: Sequence[dict[str, object]],
) -> GRCGoldContractReport:
    existing_tables = {str(row["table_name"]) for row in rows}
    actual_columns = {
        (str(row["table_name"]), str(row["column_name"])): row
        for row in rows
        if row.get("column_name") is not None
    }
    required_tables = {requirement.table for requirement in requirements}
    missing_tables = tuple(sorted(required_tables - existing_tables))
    missing_table_set = set(missing_tables)
    missing_columns = []
    incompatible_columns = []

    for requirement in requirements:
        if requirement.table in missing_table_set:
            continue
        actual = actual_columns.get((requirement.table, requirement.column))
        if actual is None:
            missing_columns.append(requirement.identity)
            continue

        actual_type = str(actual["data_type"])
        actual_length = actual.get("character_maximum_length")
        type_matches = actual_type in requirement.data_types
        length_matches = (
            requirement.minimum_character_length is None
            or actual_length is None
            or (
                isinstance(actual_length, int)
                and actual_length >= requirement.minimum_character_length
            )
        )
        if not type_matches or not length_matches:
            actual_description = actual_type
            if actual_length is not None:
                actual_description = f"{actual_description}({actual_length})"
            incompatible_columns.append(
                GRCGoldColumnTypeMismatch(
                    identity=requirement.identity,
                    expected=requirement.expected_type,
                    actual=actual_description,
                )
            )

    return GRCGoldContractReport(
        scopes=scopes,
        missing_tables=missing_tables,
        missing_columns=tuple(missing_columns),
        incompatible_columns=tuple(incompatible_columns),
    )


def check_grc_gold_contract(
    settings: GRCDWHSettings,
    *,
    scopes: Sequence[str] = ("reference", "project"),
    connection_factory: Callable[..., Any] = psycopg2.connect,
) -> GRCGoldContractReport:
    """Inspect the Gold schema without modifying either Gold or the GO cache."""

    if not isinstance(settings, GRCDWHSettings):
        raise GRCDWHConfigurationError("settings must be GRCDWHSettings")
    normalized_scopes = _normalize_scopes(scopes)
    requirements = _required_columns(normalized_scopes)

    connection_kwargs = settings.connection_kwargs()
    connection_kwargs["application_name"] = "grc_go_contract_check"
    try:
        connection = connection_factory(**connection_kwargs)
    except psycopg2.Error as exc:
        raise GRCDWHReadError("could not connect to the GRC Gold database") from exc

    try:
        connection.set_session(
            readonly=True,
            autocommit=False,
            isolation_level=extensions.ISOLATION_LEVEL_REPEATABLE_READ,
        )
        with connection.cursor() as cursor:
            cursor.execute(_SCHEMA_QUERY, (settings.schema,))
            columns = [column.name if hasattr(column, "name") else column[0] for column in cursor.description]
            rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        return _build_report(normalized_scopes, requirements, rows)
    except psycopg2.Error as exc:
        raise GRCDWHReadError("GRC Gold contract inspection query failed") from exc
    finally:
        connection.close()
