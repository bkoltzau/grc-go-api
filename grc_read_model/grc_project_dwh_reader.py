from collections.abc import Callable, Mapping
from typing import Any

import psycopg2
from psycopg2 import extensions

from grc_read_model.grc_dwh_reader import GRCDWHConfigurationError, GRCDWHReadError, GRCDWHSettings
from grc_read_model.grc_project_projection import GRCGoldProject, GRCGoldProjectDeletion
from grc_read_model.grc_project_reference import GRCGoldProjectSector
from grc_read_model.grc_project_sync import GRCGoldProjectSnapshot


def _qualified_table(schema: str, table: str) -> str:
    return f'"{schema}"."{table}"'


def _fetch_rows(cursor, query: str) -> list[dict[str, object]]:
    cursor.execute(query)
    columns = [column.name if hasattr(column, "name") else column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _project_sector_query(schema: str) -> str:
    sectors = _qualified_table(schema, "dimsector")
    projects = _qualified_table(schema, "factproject")
    project_sectors = _qualified_table(schema, "bridgeprojectsector")
    return f"""
        SELECT DISTINCT
            sector.sectorkey,
            sector.goprojectprimarysectorid,
            sector.goprojectsecondarysectortagid,
            sector.code,
            sector.name
        FROM {sectors} AS sector
        WHERE EXISTS (
            SELECT 1
            FROM {projects} AS project
            WHERE NOT project.isdeleted
              AND project.sectorkey = sector.sectorkey
        ) OR EXISTS (
            SELECT 1
            FROM {project_sectors} AS bridge
            JOIN {projects} AS project
              ON project.projectid = bridge.projectid
             AND NOT project.isdeleted
            WHERE bridge.sectorkey = sector.sectorkey
        )
        ORDER BY sector.sectorkey
    """


def _project_query(schema: str) -> str:
    projects = _qualified_table(schema, "factproject")
    dates = _qualified_table(schema, "dimdate")
    countries = _qualified_table(schema, "dimcountry")
    organizations = _qualified_table(schema, "dimorganization")
    sectors = _qualified_table(schema, "dimsector")
    statuses = _qualified_table(schema, "dimoperationstatus")
    locations = _qualified_table(schema, "dimlocation")
    project_sectors = _qualified_table(schema, "bridgeprojectsector")
    project_locations = _qualified_table(schema, "bridgeprojectlocation")
    project_operations = _qualified_table(schema, "bridgeprojectoperation")
    operations = _qualified_table(schema, "factoperation")
    events = _qualified_table(schema, "dimdisasterevent")
    disaster_types = _qualified_table(schema, "dimdisastertype")
    return f"""
        SELECT
            project.grc_source_id,
            project.projectid,
            project.goprojectid,
            project.projectname,
            reporting_country.grc_source_id AS reporting_ns_country_grc_source_id,
            project_country.grc_source_id AS project_country_grc_source_id,
            COALESCE(location_rows.district_grc_source_ids, ARRAY[]::uuid[])
                AS district_grc_source_ids,
            primary_operation.event_grc_source_id,
            primary_operation.godisastertypeid,
            primary_sector.goprojectprimarysectorid,
            COALESCE(sector_rows.goprojectsecondarysectortagids, ARRAY[]::integer[])
                AS goprojectsecondarysectortagids,
            start_date.date AS startdate,
            end_date.date AS enddate,
            project.goprojectprogrammetypeid,
            project.goprojectoperationtypeid,
            project_status.goprojectstatusid,
            project.budgetamountchf,
            project.peopletargeted,
            project.peoplereached,
            NULL::varchar(255) AS reportingcontactname,
            NULL::varchar(255) AS reportingcontactrole,
            NULL::varchar(255) AS reportingcontactemail,
            project.ingestedat,
            sector_rows.primarysectorbridgecount,
            sector_rows.unmappedsecondarysectorcount,
            location_rows.unmappeddistrictcount,
            location_rows.missinglocationcount,
            location_rows.otheradminlevelcount,
            location_rows.unknownadminlevelcount,
            location_rows.duplicatelocationcount,
            primary_operation.primaryoperationcount,
            primary_operation.unmappedeventcount,
            primary_operation.unmappeddisastertypecount
        FROM {projects} AS project
        LEFT JOIN {dates} AS start_date ON start_date.datekey = project.startdatekey
        LEFT JOIN {dates} AS end_date ON end_date.datekey = project.enddatekey
        LEFT JOIN {countries} AS project_country ON project_country.countrykey = project.countrykey
        LEFT JOIN {organizations} AS reporting_organization
            ON reporting_organization.organizationkey = project.organizationkey
        LEFT JOIN {countries} AS reporting_country
            ON reporting_country.countrykey = reporting_organization.countrykey
        LEFT JOIN {sectors} AS primary_sector ON primary_sector.sectorkey = project.sectorkey
        LEFT JOIN {statuses} AS project_status
            ON project_status.operationstatuskey = project.operationstatuskey
        LEFT JOIN LATERAL (
            SELECT
                array_agg(secondary_sector.goprojectsecondarysectortagid
                    ORDER BY secondary_sector.goprojectsecondarysectortagid)
                    FILTER (
                        WHERE bridge.sectorkey <> project.sectorkey
                          AND secondary_sector.goprojectsecondarysectortagid IS NOT NULL
                    ) AS goprojectsecondarysectortagids,
                count(*) FILTER (WHERE bridge.sectorkey = project.sectorkey)
                    AS primarysectorbridgecount,
                count(*) FILTER (
                    WHERE bridge.sectorkey <> project.sectorkey
                      AND secondary_sector.goprojectsecondarysectortagid IS NULL
                ) AS unmappedsecondarysectorcount
            FROM {project_sectors} AS bridge
            LEFT JOIN {sectors} AS secondary_sector
                ON secondary_sector.sectorkey = bridge.sectorkey
            WHERE bridge.projectid = project.projectid
        ) AS sector_rows ON TRUE
        LEFT JOIN LATERAL (
            SELECT
                array_agg(location.grc_source_id ORDER BY location.grc_source_id)
                    FILTER (
                        WHERE location.adminlevel = 1
                          AND location.grc_source_id IS NOT NULL
                    ) AS district_grc_source_ids,
                count(*) FILTER (
                    WHERE location.adminlevel = 1 AND location.grc_source_id IS NULL
                ) AS unmappeddistrictcount,
                count(*) FILTER (WHERE location.locationkey IS NULL)
                    AS missinglocationcount,
                count(*) FILTER (WHERE location.adminlevel <> 1)
                    AS otheradminlevelcount,
                count(*) FILTER (
                    WHERE location.locationkey IS NOT NULL AND location.adminlevel IS NULL
                ) AS unknownadminlevelcount,
                count(bridge.locationkey) - count(DISTINCT bridge.locationkey)
                    AS duplicatelocationcount
            FROM {project_locations} AS bridge
            LEFT JOIN {locations} AS location ON location.locationkey = bridge.locationkey
            WHERE bridge.projectid = project.projectid
        ) AS location_rows ON TRUE
        LEFT JOIN LATERAL (
            SELECT
                (array_agg(event.grc_source_id))[1] AS event_grc_source_id,
                max(disaster_type.godisastertypeid) AS godisastertypeid,
                count(*) AS primaryoperationcount,
                count(*) FILTER (WHERE event.grc_source_id IS NULL) AS unmappedeventcount,
                count(*) FILTER (WHERE disaster_type.godisastertypeid IS NULL)
                    AS unmappeddisastertypecount
            FROM {project_operations} AS bridge
            LEFT JOIN {operations} AS operation
                ON operation.operationid = bridge.operationid
               AND NOT operation.isdeleted
            LEFT JOIN {events} AS event ON event.disastereventkey = operation.disastereventkey
            LEFT JOIN {disaster_types} AS disaster_type
                ON disaster_type.disastertypekey = operation.disastertypekey
            WHERE bridge.projectid = project.projectid
              AND bridge.isprimary IS TRUE
        ) AS primary_operation ON TRUE
        WHERE NOT project.isdeleted
        ORDER BY project.grc_source_id
    """


def _project_deletion_query(schema: str) -> str:
    projects = _qualified_table(schema, "factproject")
    return f"""
        SELECT grc_source_id, projectid, goprojectid, ingestedat
        FROM {projects}
        WHERE isdeleted
        ORDER BY grc_source_id
    """


def _validate_project_relationships(row: Mapping[str, object]) -> None:
    project_id = row.get("projectid")
    if row.get("primarysectorbridgecount") != 1:
        raise GRCDWHReadError(
            f"Gold Project {project_id} must contain its primary sectorkey exactly once in bridgeprojectsector"
        )
    if row.get("unmappedsecondarysectorcount") != 0:
        raise GRCDWHReadError(
            f"Gold Project {project_id} has a non-primary sector without a GO secondary SectorTag mapping"
        )
    if row.get("unmappeddistrictcount") != 0:
        raise GRCDWHReadError(
            f"Gold Project {project_id} has an ADM1 location without a grc_source_id"
        )
    if row.get("missinglocationcount") != 0:
        raise GRCDWHReadError(f"Gold Project {project_id} has a location bridge without a dimlocation row")
    if row.get("unknownadminlevelcount") != 0:
        raise GRCDWHReadError(f"Gold Project {project_id} has a location without an adminlevel")
    if row.get("duplicatelocationcount") != 0:
        raise GRCDWHReadError(f"Gold Project {project_id} has a duplicate location bridge")
    if row.get("primaryoperationcount") not in (0, 1):
        raise GRCDWHReadError(f"Gold Project {project_id} must have at most one primary Operation")
    if row.get("primaryoperationcount") == 1 and row.get("unmappedeventcount") != 0:
        raise GRCDWHReadError(
            f"Gold Project {project_id} primary Operation must resolve to an Event grc_source_id"
        )
    if row.get("primaryoperationcount") == 1 and row.get("unmappeddisastertypecount") != 0:
        raise GRCDWHReadError(
            f"Gold Project {project_id} primary Operation must resolve to a mapped GO DisasterType"
        )


def load_grc_project_snapshot(
    settings: GRCDWHSettings,
    *,
    connection_factory: Callable[..., Any] = psycopg2.connect,
) -> GRCGoldProjectSnapshot:
    """Read one repeatable, read-only Project/sector/tombstone snapshot from Gold."""

    if not isinstance(settings, GRCDWHSettings):
        raise GRCDWHConfigurationError("settings must be GRCDWHSettings")

    connection_kwargs = settings.connection_kwargs()
    connection_kwargs["application_name"] = "grc_go_project_sync"
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
            watermark_rows = _fetch_rows(cursor, "SELECT CURRENT_TIMESTAMP AS watermark")
            if len(watermark_rows) != 1:
                raise GRCDWHReadError("Gold transaction watermark query did not return exactly one row")

            sector_rows = _fetch_rows(cursor, _project_sector_query(settings.schema))
            project_rows = _fetch_rows(cursor, _project_query(settings.schema))
            deletion_rows = _fetch_rows(cursor, _project_deletion_query(settings.schema))

        for row in project_rows:
            _validate_project_relationships(row)

        return GRCGoldProjectSnapshot(
            sectors=tuple(GRCGoldProjectSector.from_gold_row(row) for row in sector_rows),
            projects=tuple(GRCGoldProject.from_gold_row(row) for row in project_rows),
            deletions=tuple(GRCGoldProjectDeletion.from_gold_row(row) for row in deletion_rows),
            watermark=watermark_rows[0]["watermark"],
        )
    except psycopg2.Error as exc:
        raise GRCDWHReadError("GRC Gold Project snapshot query failed") from exc
    finally:
        connection.close()
