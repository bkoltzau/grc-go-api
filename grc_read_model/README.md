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

Publication into these tables is conditional on their existing required fields. For example, a GO `Project` requires a reporting National Society, primary sector, start date, and end date even though the corresponding Gold foreign keys are nullable. The publisher must quarantine an incomplete row rather than invent a fallback. `Project.save()` also derives status from its dates, so the future Project publisher must validate that the mapped Gold status agrees with GO's date-based status instead of silently overriding one source with the other.

The serving API should reuse upstream's existing `DJANGO_READ_ONLY=true` setting. The future scheduled publisher must run separately with write access and narrowly scoped database credentials; read-only mode should not be disabled on the serving API to accommodate synchronization.

The known version-one mismatch is Operation funding. GO's `Appeal` model requires numeric requested and funded amounts, while the approved Gold contract defers those values. The future publisher must not write guessed zeroes. That mismatch remains documented debt and requires an isolated adapter or a later Gold financial source before Operations can expose those fields truthfully.

## Metadata tables

- `grc_source_record` maps a stable Gold source identity to the integer primary key of the GO cache row. Its source hash and ingestion timestamp support idempotent and incremental processing. `is_deleted` records Gold soft-delete state.
- `grc_sync_run` records a publication attempt, its source watermark range, counters, status, and failure details.
- `grc_read_model_state` stores the last successfully published watermark per source stream.

The metadata does not own or duplicate Country, Project, Operation, or Activity payload fields.

## Country projection

`grc_country_projection.py` is the first narrow publication adapter. It accepts a
complete, already-fetched `dimcountry` snapshot matching the approved Gold
contract, validates it before writing, and transactionally updates the existing
`api.Region` and `api.Country` tables plus `GRCSourceRecord` metadata.

It deliberately preserves upstream Country fields that Gold version 1 does not
own. It uses `gocountryid` and `goregionid` as the existing GO primary keys,
uses `gocountryid` as the stable publication-metadata identity,
maps inactive Gold rows to GO's `is_deprecated`, distinguishes that from a Gold
soft delete, resolves sovereign-country links in a second pass, and rejects
unknown GO enum values, duplicate identifiers, invalid geometry, conflicting
Region values, and unresolved sovereign keys.

The publisher requires a complete snapshot because dimension CDC remains an
open DWH question. It receives records from its caller; it does not connect to
Gold or schedule itself. It also rejects timezone-naive source timestamps until
the DWH timestamp convention is confirmed rather than assuming UTC or local
time.

The function returns per-batch counters; the future orchestration layer owns
aggregating them into `GRCSyncRun` and advancing `GRCReadModelState` only after
all dependency-safe publishers succeed.

## District/ADM1 projection

`grc_district_projection.py` publishes only explicitly mapped ADM1
`dimlocation` rows into the existing `api.District` table. Gold must provide the
stable upstream `godistrictid`; the DWH `locationkey` cannot replace it because
the existing map boundary layer and API relations use GO District IDs.

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

The `bridgeprojectsector` meaning and the controlled organization role/type
values remain data-contract blockers for Project publication. No Project row or
many-to-many relationship is written until those semantics are confirmed.

## Disaster Event projection

`grc_event_projection.py` validates existing GO DisasterType IDs and publishes
active `dimdisasterevent` records into `api.Event`. It requires explicit stable
GO Event and DisasterType IDs, complete Event country and ADM1 collections, an
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

## Future publication rules

The future `grc_sync` implementation should:

1. Read and transform Gold data outside request handling.
2. Resolve stable identities through `GRCSourceRecord`.
3. Publish one dependency-safe batch inside `transaction.atomic()`.
4. Apply `isdeleted` without confusing a missing value with a deletion.
5. Advance `GRCReadModelState.last_successful_watermark` only after the domain rows and source records commit successfully.
6. Mark a failed `GRCSyncRun` without changing the last successful watermark or partially publishing a batch.

No DWH connection, scheduler, orchestration command, API switch, or authentication change is implemented in this phase.
