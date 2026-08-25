# GRC read-model boundary

This app owns metadata for publishing the GRC Gold projection into GO. It does not contain a second copy of the GO domain model and does not query Gold during HTTP requests.

## Serving boundary

The existing GO ORM tables remain the read-model cache for fields that have the same semantics:

- `api.Country` and `api.District` for geography and Country pages
- `api.Event` for disaster events
- `api.Appeal` for Operations where the Gold contract is compatible
- `deployments.Project` for the retired read-only Project experience
- `deployments.EmergencyProject` and `deployments.EmergencyProjectActivity` for Activity responses
- Existing GO reference tables for disaster types, sectors, and controlled values

This keeps the existing viewsets, filters, serializers, pagination, and frontend response properties unchanged. A GRC adapter is required only where Gold and GO are not semantically equivalent.

Publication into these tables is conditional on their existing required fields. For example, a GO `Project` requires a reporting National Society, primary sector, start date, and end date even though the corresponding Gold foreign keys are nullable. The publisher rejects an incomplete batch rather than inventing a fallback. `Project.save()` also derives status from its dates, so the Project publisher validates that the mapped Gold status agrees with GO's date-based status instead of silently preferring either value.

The serving API should reuse upstream's existing `DJANGO_READ_ONLY=true` setting. The future scheduled publisher must run separately with write access and narrowly scoped database credentials; read-only mode should not be disabled on the serving API to accommodate synchronization.

The known version-one mismatch is Operation funding. GO's `Appeal` model requires numeric requested and funded amounts, while the approved Gold contract defers those values. The future publisher may use explicit `0.0` compatibility placeholders, documented and labelled as unavailable rather than observed zero. They must not feed aggregates. Authoritative financial publication remains later DWH work.

## Metadata tables

- `grc_source_record` maps a DWH-owned immutable `grc_source_id` UUID to the integer primary key of the GO cache row. Its source hash and ingestion timestamp support idempotent complete-snapshot comparison. `is_deleted` records Gold soft-delete state.
- `grc_sync_run` records a publication attempt, its source watermark range, counters, status, and failure details.
- `grc_read_model_state` stores the last successfully published watermark per source stream.

The metadata does not own or duplicate Country, Project, Operation, or Activity payload fields.

## Country projection

`grc_country_projection.py` is the first narrow publication adapter. It accepts a
complete, already-fetched `dimcountry` snapshot matching the approved Gold
contract, validates it before writing, and transactionally updates the existing
`api.Region` and `api.Country` tables plus `GRCSourceRecord` metadata.

It deliberately preserves upstream Country fields that Gold version 1 does not
own. It uses `grc_source_id` as the stable Gold identity. An optional
`gocountryid` preserves the original target for a GO-sourced row; a GRC-only row
receives a normal GO integer ID and retains it through `GRCSourceRecord`.
It uses `goregionid` for the existing Region primary key,
maps inactive Gold rows to GO's `is_deprecated`, distinguishes that from a Gold
soft delete, resolves sovereign-country links in a second pass, and rejects
unknown GO enum values, duplicate identifiers, invalid geometry, conflicting
Region values, and unresolved sovereign keys.

The publisher receives a complete snapshot and computes deterministic content
hashes every two hours. It does not connect to Gold or schedule itself. It
rejects timezone-naive source timestamps; existing naive DWH values are
explicitly interpreted as Berlin local time and exposed as `timestamptz`.

The function returns per-batch counters; the future orchestration layer owns
aggregating them into `GRCSyncRun` and advancing `GRCReadModelState` only after
all dependency-safe publishers succeed.

## District/ADM1 projection

`grc_district_projection.py` publishes only explicitly mapped ADM1
`dimlocation` rows into the existing `api.District` table. Gold provides an
immutable `grc_source_id`; optional `godistrictid` preserves an original GO
target while GRC-only ADM1 rows receive a stable allocated GO integer target.
The DWH `locationkey` remains a relationship surrogate, not cross-load identity.

The adapter requires Country publication to complete first, validates GO's
shorter name/code limits and the ADM1 level, derives the centroid from Gold
latitude/longitude, and maps `isactive` to `is_deprecated`. It intentionally
leaves GO-only fields such as `bbox`, `is_enclave`, population, NUTS, EMMA, and
FIPS untouched. District bounding boxes are not required by the version-one
Country Project map.

## Project reference contract

`grc_project_reference.py` validates Project controlled values and sector
dependencies without publishing a Project. Programme type, Project operation
type, and Project status must be the exact integer values from the existing GO
enums; translated labels are never used as mapping keys. It also reproduces
the status calculation in upstream `Project.save()` and rejects a Gold status
that disagrees with the start/end dates at the supplied publication date.

Primary `Sector` and secondary `SectorTag` are deliberately treated as separate
GO identity domains. Gold must carry an explicit ID for each applicable domain,
including zero as a valid upstream ID. The validator requires those IDs to
already exist in the upstream reference tables; it does not create sectors or
match them by name.

The approved `bridgeprojectsector` meaning is the complete Project-sector set.
The primary `factproject.sectorkey` must occur exactly once in the bridge and is
excluded from GO's secondary tags; every other bridge row must resolve through
an explicit GO `SectorTag` ID. `factproject.organizationkey` is the reporting
National Society organization and resolves to its GO Country through
`dimorganization.countrykey`, `dimcountry.grc_source_id`, and the publication mapping.

## Project publication

`grc_project_projection.py` publishes the approved Gold Project fields into the
existing `deployments.Project` table. It preserves the current Project API,
filters, serializer and frontend contracts. It resolves Country, reporting NS,
District and Event relationships through published Gold UUID mappings, while
DisasterType, primary Sector and secondary SectorTag remain exact GO-controlled
IDs. It rejects missing dependencies and cross-country Districts, and
sets internal `MEMBERSHIP` visibility.

The publisher maps the overall budget and target/reached totals, deliberately
clears unavailable expenditure, sex-disaggregated values and stale annual
splits so the frontend uses those authoritative overall totals, and does not
manufacture a modifying user. Whole-CHF validation prevents a decimal Gold
amount from being silently rounded into GO's integer field. Non-ADM1 Project
locations remain in Gold and are not coerced into Districts; `project_admin2`
is not needed by the version-one Project UI.

Gold Project tombstones delete the corresponding serving-cache Project and
mark its `GRCSourceRecord` deleted. Active rows and tombstones are idempotent and
cannot overlap in one snapshot. `grc_project_sync.py` performs sector-reference
validation, Project publication, run accounting and watermark advancement in
one outer transaction, leaving the prior cache and watermark intact on failure.

## Disaster Event projection

`grc_event_projection.py` validates existing GO DisasterType IDs and publishes
active `dimdisasterevent` records into `api.Event`. It requires an immutable
Gold Event UUID, an optional original GO Event ID, an exact GO DisasterType ID,
complete Event country and ADM1 UUID collections, an
authoritative Event title and start timestamp, and exact GO severity values.

Countries, Districts, and their Regions must already be projected. The adapter
sets the Event's complete `countries`, `countries_for_preview`, `districts`, and
derived `regions` relationships transactionally, rejecting a District outside
the Event's country set. It owns only Gold-backed scalar fields and preserves
upstream-only fields and relations such as slug, parent, contacts, links,
documents, field reports, tabs, images, and map-display flags.

Projected Events use upstream `MEMBERSHIP` visibility. This makes them visible
to authenticated users through the existing visibility filter and excludes
them from anonymous Event queries, matching the internal-only GRC deployment
without implementing authentication in this phase.

Inactive Events are deliberately rejected because upstream Event has no
deprecation field. Safe deletion reconciliation remains orchestration work; the
active publisher does not guess by assigning a different visibility or parent.

## Publication invariants

Every GRC publisher should:

1. Read and transform Gold data outside request handling.
2. Resolve stable identities through `GRCSourceRecord`.
3. Publish one dependency-safe batch inside `transaction.atomic()`.
4. Apply `isdeleted` without confusing a missing value with a deletion.
5. Advance `GRCReadModelState.last_successful_watermark` only after the domain rows and source records commit successfully.
6. Mark a failed `GRCSyncRun` without changing the last successful watermark or partially publishing a batch.

No DWH credentials, scheduler, request-time API switch, or authentication change is implemented in this phase.

## Reference snapshot orchestration

`grc_reference_sync.py` composes the Country, District, and Event publishers in
their required dependency order. Its input is a typed, immutable
`GRCGoldReferenceSnapshot`; the DWH reader is kept as a separate adapter.

The orchestration creates a `GRCSyncRun`, locks the applicable
`GRCReadModelState`, rejects a regressive or timezone-naive watermark, validates
the existing DisasterType identities, and publishes all three domains inside
one outer database transaction. The watermark and successful run state advance
in that same transaction. If any downstream projection fails, all serving-cache
and source-record changes roll back, the previous successful watermark remains
unchanged, and the run is retained with failed status and the error type.
Source ingestion timestamps later than the repeatable-read transaction
watermark are also rejected, preventing a run from claiming a checkpoint older
than data it has already published.

A complete Country snapshot must be non-empty so an upstream extraction failure
cannot be mistaken for a valid publication. District and Event snapshots may be
empty. Equal watermarks are accepted to support idempotent re-runs; older
watermarks are rejected.

This orchestration layer does not choose a DWH connection, issue Gold SQL,
schedule itself, apply deletions, or guess unresolved mappings. The adjacent
reader and management command described below construct the typed snapshot and
call this orchestrator from a separately write-authorized process.

## Gold readers and management commands

`grc_dwh_reader.py` is the isolated source repository for the reference
snapshot. It uses the PostgreSQL driver already required by upstream GO and does
not add the Gold database to Django's ORM configuration. The reader opens a
short-lived connection with a read-only, repeatable-read transaction, takes its
watermark from `CURRENT_TIMESTAMP` inside that transaction, loads the four
approved reference projections, constructs the typed snapshot, and closes the
connection before publication begins.

Configure the future sync process with:

```env
GRC_DWH_DB_HOST=
GRC_DWH_DB_PORT=5432
GRC_DWH_DB_NAME=
GRC_DWH_DB_USER=
GRC_DWH_DB_PASSWORD=
GRC_DWH_DB_SCHEMA=public
GRC_DWH_DB_SSLMODE=require
GRC_DWH_DB_CONNECT_TIMEOUT=10
```

The password is excluded from the configuration object's representation. The
schema accepts only one unquoted PostgreSQL identifier and is quoted before use.
The connection should also use database credentials that have only `SELECT`
permission on the required Gold objects.

The readers deliberately query the version-one additions documented in
`docs/grc_gold_contract_additions.md`. They will fail until those fields and the
Event country/location bridges exist. It rejects incomplete Event geography,
including unmapped bridge rows, missing or multiple primary-country flags, and a
primary bridge that disagrees with `dimdisasterevent.primarycountrykey`. Event
location bridges may contain ADM2 or deeper locations; version 1 validates but
projects only mapped ADM1 rows into GO District relationships. Missing
`dimlocation` targets, null administrative levels, and duplicate location links
are rejected instead of being silently ignored.

`grc_project_dwh_reader.py` independently loads Project sectors, active Project
rows, relationships and fact tombstones in another read-only repeatable-read
snapshot. It enforces the approved all-sector bridge rule, accepts non-ADM1
location rows without projecting them, rejects missing/ambiguous/duplicate
location relationships, and requires at most one primary Operation whose Event
through UUID publication mappings and whose DisasterType resolves to an exact GO ID.

Before extraction, `grc_dwh_contract.py` can inspect the configured Gold
database's `information_schema` without writing to Gold or GO. It validates the
tables, columns, character capacity, and timezone-aware timestamp types used by
the implemented reference and Project readers. The companion
`docs/grc_gold_contract_additions.sql` is a DWH-team review template, not an
application migration. Its reviewed conversion interprets existing naive fact
timestamps as Berlin local time (`Europe/Berlin`; Microsoft `W. Europe Standard Time`).

Run the read-only preflight with either or both scopes:

```bash
python manage.py grc_check_dwh_contract --scope reference --scope project
```

This command is safe while the serving process uses `DJANGO_READ_ONLY=true`.
It checks schema compatibility, not data completeness or mapping correctness;
the typed readers and publishers continue to enforce row-level semantics.

Run a configured, write-authorized publication process with:

```bash
DJANGO_READ_ONLY=false python manage.py grc_sync_reference
DJANGO_READ_ONLY=false python manage.py grc_sync_projects
```

Both commands refuse to execute when `DJANGO_READ_ONLY=true`, then delegate
extraction and transactional publication to their domain orchestrators. They do
not schedule themselves, store DWH credentials, add fallback values, or alter
request-time API queries. Reference publication must complete before Project
publication so the required GO cache identities exist.

Each publication emits start, success, and failure records through Django's
existing logging configuration with the run ID, pipeline, watermark, and row
counters. Inspect the durable database state without enabling writes using:

```bash
python manage.py grc_sync_status
python manage.py grc_sync_status --json
```

The JSON form is suitable for a later scheduler or monitoring probe. This is a
read-only status surface, not an alerting or metrics-backend integration.
