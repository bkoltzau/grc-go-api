import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import psycopg2
from psycopg2 import extensions

from grc_read_model.grc_country_projection import GRCGoldCountry
from grc_read_model.grc_district_projection import GRCGoldDistrict
from grc_read_model.grc_event_projection import GRCGoldDisasterEvent, GRCGoldDisasterType
from grc_read_model.grc_reference_sync import GRCGoldReferenceSnapshot


class GRCDWHConfigurationError(ValueError):
    pass


class GRCDWHReadError(RuntimeError):
    pass


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SSL_MODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}


def _required_environment_value(environ: Mapping[str, str], key: str) -> str:
    value = environ.get(key, "").strip()
    if not value:
        raise GRCDWHConfigurationError(f"{key} is required")
    return value


def _positive_integer(value: str, key: str, *, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise GRCDWHConfigurationError(f"{key} must be an integer") from None
    if parsed <= 0 or (maximum is not None and parsed > maximum):
        qualifier = f" between 1 and {maximum}" if maximum is not None else " positive"
        raise GRCDWHConfigurationError(f"{key} must be{qualifier}")
    return parsed


@dataclass(frozen=True)
class GRCDWHSettings:
    host: str
    name: str
    user: str
    password: str = field(repr=False)
    port: int = 5432
    schema: str = "public"
    sslmode: str = "require"
    connect_timeout: int = 10

    def __post_init__(self):
        for field_name in ("host", "name", "user"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise GRCDWHConfigurationError(f"{field_name} must not be empty")
            object.__setattr__(self, field_name, value.strip())
        if not isinstance(self.password, str) or not self.password:
            raise GRCDWHConfigurationError("password must not be empty")
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise GRCDWHConfigurationError("port must be between 1 and 65535")
        if (
            not isinstance(self.connect_timeout, int)
            or isinstance(self.connect_timeout, bool)
            or self.connect_timeout <= 0
        ):
            raise GRCDWHConfigurationError("connect_timeout must be positive")
        if not isinstance(self.schema, str) or not _IDENTIFIER_PATTERN.fullmatch(self.schema):
            raise GRCDWHConfigurationError("schema must be a single unquoted PostgreSQL identifier")
        object.__setattr__(self, "schema", self.schema.strip())
        if not isinstance(self.sslmode, str) or self.sslmode.lower() not in _SSL_MODES:
            values = ", ".join(sorted(_SSL_MODES))
            raise GRCDWHConfigurationError(f"sslmode must be one of: {values}")
        object.__setattr__(self, "sslmode", self.sslmode.lower())

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "GRCDWHSettings":
        source = os.environ if environ is None else environ
        schema = source.get("GRC_DWH_DB_SCHEMA", "public").strip() or "public"
        sslmode = source.get("GRC_DWH_DB_SSLMODE", "require").strip().lower() or "require"
        return cls(
            host=_required_environment_value(source, "GRC_DWH_DB_HOST"),
            name=_required_environment_value(source, "GRC_DWH_DB_NAME"),
            user=_required_environment_value(source, "GRC_DWH_DB_USER"),
            password=_required_environment_value(source, "GRC_DWH_DB_PASSWORD"),
            port=_positive_integer(source.get("GRC_DWH_DB_PORT", "5432"), "GRC_DWH_DB_PORT", maximum=65535),
            schema=schema,
            sslmode=sslmode,
            connect_timeout=_positive_integer(
                source.get("GRC_DWH_DB_CONNECT_TIMEOUT", "10"),
                "GRC_DWH_DB_CONNECT_TIMEOUT",
            ),
        )

    def connection_kwargs(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.name,
            "user": self.user,
            "password": self.password,
            "sslmode": self.sslmode,
            "connect_timeout": self.connect_timeout,
            "application_name": "grc_go_reference_sync",
        }


def _qualified_table(schema: str, table: str) -> str:
    return f'"{schema}"."{table}"'


def _fetch_rows(cursor, query: str) -> list[dict[str, object]]:
    cursor.execute(query)
    columns = [column.name if hasattr(column, "name") else column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _validate_event_geography(row: Mapping[str, object]) -> None:
    event_id = row.get("goeventid")
    if row.get("unmappedcountrycount") != 0:
        raise GRCDWHReadError(f"GO Event {event_id} has a country bridge without a mapped gocountryid")
    if row.get("unmappeddistrictcount") != 0:
        raise GRCDWHReadError(f"GO Event {event_id} has a location bridge without a mapped godistrictid")
    if row.get("bridgeprimarycount") != 1:
        raise GRCDWHReadError(f"GO Event {event_id} must have exactly one primary country bridge")
    if row.get("primarygocountryid") is None:
        raise GRCDWHReadError(f"GO Event {event_id} primarycountrykey must resolve to a mapped gocountryid")
    if row.get("bridgeprimarygocountryid") != row.get("primarygocountryid"):
        raise GRCDWHReadError(
            f"GO Event {event_id} primary country bridge does not match dimdisasterevent.primarycountrykey"
        )
    if not row.get("gocountryids"):
        raise GRCDWHReadError(f"GO Event {event_id} must have at least one mapped country bridge")


def _country_query(schema: str) -> str:
    table = _qualified_table(schema, "dimcountry")
    return f"""
        SELECT
            countrykey,
            gocountryid,
            goregionid,
            goregionnameid,
            gorecordtypeid,
            name,
            iso2,
            iso3,
            region,
            independentflag,
            isactive,
            societyname,
            sovereigncountrykey,
            centroidlatitude,
            centroidlongitude,
            bboxwest,
            bboxsouth,
            bboxeast,
            bboxnorth,
            sourceupdatedat,
            ingestedat
        FROM {table}
        WHERE gocountryid IS NOT NULL
        ORDER BY gocountryid
    """


def _district_query(schema: str) -> str:
    locations = _qualified_table(schema, "dimlocation")
    countries = _qualified_table(schema, "dimcountry")
    return f"""
        SELECT
            location.locationkey,
            location.godistrictid,
            country.gocountryid,
            location.countrykey,
            location.adminlevel,
            location.pcode,
            location.name,
            location.latitude,
            location.longitude,
            location.isactive,
            location.sourceupdatedat,
            location.ingestedat
        FROM {locations} AS location
        LEFT JOIN {countries} AS country ON country.countrykey = location.countrykey
        WHERE location.godistrictid IS NOT NULL
        ORDER BY location.godistrictid
    """


def _disaster_type_query(schema: str) -> str:
    table = _qualified_table(schema, "dimdisastertype")
    return f"""
        SELECT disastertypekey, godisastertypeid, code, name, isactive
        FROM {table}
        WHERE godisastertypeid IS NOT NULL
        ORDER BY godisastertypeid
    """


def _event_query(schema: str) -> str:
    events = _qualified_table(schema, "dimdisasterevent")
    disaster_types = _qualified_table(schema, "dimdisastertype")
    countries = _qualified_table(schema, "dimcountry")
    locations = _qualified_table(schema, "dimlocation")
    event_countries = _qualified_table(schema, "bridgedisastereventcountry")
    event_locations = _qualified_table(schema, "bridgedisastereventlocation")
    return f"""
        SELECT
            event.disastereventkey,
            event.goeventid,
            disaster_type.godisastertypeid,
            event.name,
            event.glide,
            event.disasterstartat,
            event.description,
            event.peopleaffected,
            event.goifrcseveritylevelid,
            event.ifrcseveritylevelupdatedat,
            country_rows.gocountryids,
            COALESCE(location_rows.godistrictids, ARRAY[]::integer[]) AS godistrictids,
            event.isactive,
            event.sourceupdatedat,
            event.ingestedat,
            primary_country.gocountryid AS primarygocountryid,
            country_rows.bridgeprimarycount,
            country_rows.bridgeprimarygocountryid,
            country_rows.unmappedcountrycount,
            COALESCE(location_rows.unmappeddistrictcount, 0) AS unmappeddistrictcount
        FROM {events} AS event
        JOIN {disaster_types} AS disaster_type
            ON disaster_type.disastertypekey = event.disastertypekey
        LEFT JOIN {countries} AS primary_country
            ON primary_country.countrykey = event.primarycountrykey
        JOIN LATERAL (
            SELECT
                array_agg(country.gocountryid ORDER BY country.gocountryid)
                    FILTER (WHERE country.gocountryid IS NOT NULL) AS gocountryids,
                count(*) FILTER (WHERE bridge.isprimary) AS bridgeprimarycount,
                max(country.gocountryid) FILTER (WHERE bridge.isprimary)
                    AS bridgeprimarygocountryid,
                count(*) FILTER (WHERE country.gocountryid IS NULL) AS unmappedcountrycount
            FROM {event_countries} AS bridge
            LEFT JOIN {countries} AS country ON country.countrykey = bridge.countrykey
            WHERE bridge.disastereventkey = event.disastereventkey
        ) AS country_rows ON TRUE
        LEFT JOIN LATERAL (
            SELECT
                array_agg(location.godistrictid ORDER BY location.godistrictid)
                    FILTER (WHERE location.godistrictid IS NOT NULL) AS godistrictids,
                count(*) FILTER (WHERE location.godistrictid IS NULL) AS unmappeddistrictcount
            FROM {event_locations} AS bridge
            LEFT JOIN {locations} AS location ON location.locationkey = bridge.locationkey
            WHERE bridge.disastereventkey = event.disastereventkey
        ) AS location_rows ON TRUE
        WHERE event.goeventid IS NOT NULL
        ORDER BY event.goeventid
    """


def load_grc_reference_snapshot(
    settings: GRCDWHSettings,
    *,
    connection_factory: Callable[..., Any] = psycopg2.connect,
) -> GRCGoldReferenceSnapshot:
    """Read one repeatable, read-only Country/District/Event snapshot from Gold."""

    if not isinstance(settings, GRCDWHSettings):
        raise GRCDWHConfigurationError("settings must be GRCDWHSettings")

    try:
        connection = connection_factory(**settings.connection_kwargs())
    except psycopg2.Error as exc:
        raise GRCDWHReadError("could not connect to the GRC Gold database") from exc

    try:
        connection.set_session(
            readonly=True,
            autocommit=False,
            isolation_level=extensions.ISOLATION_LEVEL_REPEATABLE_READ,
        )
        with connection.cursor() as cursor:
            watermark_rows = _fetch_rows(cursor, "SELECT CURRENT_TIMESTAMP AS watermark")
            if len(watermark_rows) != 1:
                raise GRCDWHReadError("Gold transaction watermark query did not return exactly one row")

            country_rows = _fetch_rows(cursor, _country_query(settings.schema))
            district_rows = _fetch_rows(cursor, _district_query(settings.schema))
            disaster_type_rows = _fetch_rows(cursor, _disaster_type_query(settings.schema))
            event_rows = _fetch_rows(cursor, _event_query(settings.schema))

        for row in event_rows:
            _validate_event_geography(row)

        return GRCGoldReferenceSnapshot(
            countries=tuple(GRCGoldCountry.from_gold_row(row) for row in country_rows),
            districts=tuple(GRCGoldDistrict.from_gold_row(row) for row in district_rows),
            disaster_types=tuple(GRCGoldDisasterType.from_gold_row(row) for row in disaster_type_rows),
            events=tuple(GRCGoldDisasterEvent.from_gold_row(row) for row in event_rows),
            watermark=watermark_rows[0]["watermark"],
        )
    except psycopg2.Error as exc:
        raise GRCDWHReadError("GRC Gold reference snapshot query failed") from exc
    finally:
        connection.close()
