# GRC GO handover

Status date: 2026-08-25

This is the continuation point for the GRC/DRK adaptation of IFRC GO. The implementation starts from fresh IFRC GO forks and follows the minimal-diff rule: preserve upstream pages and API contracts, prefer configuration and isolated `grc_` adapters, and keep the DWH behind the existing GO serving model.

## Repository baseline

| Repository | Local checkout | Branch | Starting baseline | Remote branch |
|---|---|---|---|---|
| API | `C:\Users\koltzaub\Code\GRC-GO\grc-go-api` | `grc/read-model` | `31709e79` (`Add Gold contract checks and sync safeguards`) | `bkoltzau/grc-go-api:grc/read-model` |
| Web | `C:\Users\koltzaub\Code\grc-go-web-app` | `grc/project-ui` | `81954fe9` (`Restore GRC project views and module configuration`) | `bkoltzau/grc-go-web-app:grc/project-ui` |

Both repositories have read-only `upstream` remotes pointing at the corresponding IFRC GO repositories. The personal branches are intentional; no pull request has been opened yet.

Authoritative external inputs:

- Gold schema: `C:\Users\koltzaub\Code\GRC-GO\References\dwh_schema.sql`
- Original working copy of the Gold additions document: `C:\Users\koltzaub\OneDrive - Deutsches Rotes Kreuz e.V\Dokumente\dwh\Division 6 DWH\grc_gold_contract_additions.md`
- Tracked copy: `docs/grc_gold_contract_additions.md`

Do not commit `.env` files, credentials, generated database data, or GitHub tokens.

## Confirmed product decisions

- Reuse the retired upstream 3W Project UI, including its Country dashboard, list, and detail view. It was retired because the upstream data source was inadequate, not because the user experience was rejected.
- Preserve existing Country, Project, Operation, and Overview page contracts wherever possible.
- `dimdisasterevent` maps to GO Event/Emergency; `factoperation` maps to GO Appeal/Operation.
- GO reference data is ingested into Gold first, optional GRC corrections happen in Gold, and GO then consumes the projected data from Gold.
- Gold remains the structured-data source of truth. GO ORM tables act as the serving/read-model cache.
- The deployment is read-only from the user perspective. Synchronization will use a separate write-capable process later.
- Funding belongs to Projects. Detailed financial data is deferred to the next DWH phase. Because upstream Appeal fields are non-null, version 1 may use explicit `0.0` compatibility placeholders that mean "not yet supplied" and must never feed aggregates.
- Gold assigns an immutable `grc_source_id` UUID to every entity. GO-sourced rows may also carry the exact original GO integer ID; GRC-only rows receive a GO target ID during publication and retain it through the UUID mapping.
- Publish every valid in-scope Country/Event/reference row and every valid non-deleted Project; no additional business filter applies in version 1.
- Formally use repeatable-read complete snapshots plus deterministic content-hash comparison every two hours. Every consumed bridge is a complete relationship snapshot.
- Existing naive timestamps are Berlin local civil time: `W. Europe Standard Time` for Microsoft systems and `Europe/Berlin` for PostgreSQL.
- `adminlevel = 1` is ADM1 and `adminlevel = 2` is ADM2. Version 1 projects only ADM1; deeper levels remain valid Gold data.
- Operation type uses exact `goappealtypeid`; the DWH must assign it when an Operation has no GO origin. The adapter does not match labels.
- Existing GO controlled values are reused for sectors, indicators, disaggregation, disaster types, and other compatible reference domains.
- English is the only required language for version 1.
- Existing GO document/link metadata with stable SharePoint URLs is the planned version-1 document approach. No SharePoint binary-copy integration is implemented.
- Full Country NS/Profile/databank data and SharePoint integration are version-2 work.
- Authentication is not implemented. The future target is a thin trusted Microsoft/Entra proxy adapter.

## Implemented API foundation

The `grc_read_model` Django app is registered through the one-line addition to `main/settings.py`. It contains publication metadata only and deliberately does not duplicate the GO domain model.

Implemented metadata tables:

- `grc_source_record`: stable Gold identity to GO cache-row identity, content hash, ingestion time, and soft-delete state.
- `grc_sync_run`: publication attempt, counters, status, watermark range, and failure details.
- `grc_read_model_state`: last successfully published watermark per source stream.

The source identity is now a canonical DWH UUID. `GRCSourceRecord` maps it to
the existing GO integer primary key, preserving every upstream API/frontend
route. A database constraint prevents two UUIDs in the same source/entity
stream from claiming one GO target. Before allocating a GRC-only integer ID,
the publisher advances the PostgreSQL sequence past explicit GO IDs, existing
rows, and retained tombstone mappings without rewinding it.

Implemented projection/validation components:

- Country snapshot projection into existing `api.Region` and `api.Country` rows.
- ADM1 location projection into existing `api.District` rows.
- Disaster Event projection into existing `api.Event` rows and geography relations.
- Project controlled-value, status, and sector reference validation.
- Project fact/relationship publication into the existing `deployments.Project` contract, including Gold tombstones.
- Atomic Country/District/Event snapshot orchestration with run tracking, dependency ordering, failure rollback, and successful watermark advancement.
- Atomic Project snapshot orchestration with run tracking, failure rollback, and successful watermark advancement.
- An isolated PostgreSQL Gold reader using a read-only repeatable-read transaction, typed snapshot construction, and strict Event-bridge validation.
- An isolated Project Gold reader enforcing the approved all-sector bridge and primary-Operation rules.
- A `grc_sync_reference` management command that connects the Gold reader to the transactional publisher and refuses serving-process read-only mode.
- A `grc_sync_projects` management command using the same separately write-authorized process boundary.
- A read-only `grc_check_dwh_contract` preflight for the implemented reference and Project reader schemas.
- A read-only `grc_sync_status` command for durable watermarks, run outcomes, and row counters, including JSON output.
- Start/success/failure logging for reference and Project publications using Django's existing logging configuration.
- A review-only `docs/grc_gold_contract_additions.sql` DWH DDL template; GO does not execute or apply it.
- Contract tests protecting existing Country, Project, and Operation API shapes.

The detailed boundary and validation rules are in `grc_read_model/README.md`.

Not implemented yet:

- DWH credentials, deployment wiring, and execution against an updated Gold schema.
- DWH-team application/backfill of the reviewed Gold DDL template and its timezone migration decision.
- Scheduled two-hour execution.
- Reference deletion reconciliation, quarantine storage, external metrics collection, or alerting.
- Operation/Appeal publication.
- Activity, Funding, or Indicator publication.
- API-source switching beyond publishing into the existing GO ORM cache.
- Trusted-proxy authentication.

## Implemented web foundation

The web branch restores the retired upstream 3W Project experience and retains its existing API contracts:

- Country Project dashboard.
- Project list.
- Project detail.
- Supporting filters, map, and disaggregated-output components.

Module visibility uses the existing runtime configuration path with one optional allowlist:

```env
APP_GRC_ENABLED_MODULES=country,project,operation,overview
```

The allowlist is parsed centrally in `app/src/grc_module_config.ts`. When absent, upstream visibility is preserved. When configured, unknown values fail startup and new upstream modules remain hidden by default. Routes are not removed; this is discoverability configuration rather than a security boundary. See `GRC_MODULE_VISIBILITY.md`.

The initial GRC configuration hides the language selector and retains English. Authentication controls are intentionally unchanged.

## Gold contract and blockers

`docs/grc_gold_contract_additions.md` is the current detailed data contract. Important mandatory additions include:

- Stable GO Country, Region, District, DisasterType, and Event identities.
- Country record-type, independence, sovereign-country, centroid, and bounding-box fields.
- Complete Event country/location bridges.
- Authoritative `projectname`, `operationname`, `operationcode`, and `activityname` fields.
- Exact GO enum identities for Project programme/operation/status and Appeal status.
- Explicit primary and secondary Project-sector identities.
- Controlled organization types and Project/Activity organization roles.

Do not invent mappings. In particular, Event names are not Operation names, activity-type names are not Activity titles, and organization names do not prove National Society identity. Operation funding placeholders are explicit technical debt, not financial observations.

Open data-contract decisions:

- Confirm how deployed ERUs occur and are identified.
- Approve the exact GO indicator/disaggregation mapping and non-double-counting grain.
- Backfill exact `goappealtypeid`, an authoritative Appeal `aid`, and timestamp-level Operation dates before enabling Operation publication.
- Decide the UI label for temporary Operation financial placeholders.

Deferred user input (explicitly postponed after the 2026-08-25 decisions):

- Activity publication grain and deployed-ERU representation
- Remaining Project/Activity organization-role semantics
- Invalid-row quarantine and partial-publication policy beyond current fail-safe transactions
- Frontend read-only affordances beyond the existing module configuration
- DWH credentials, environment-specific deployment wiring, scheduler ownership, monitoring, and alerting
- SharePoint metadata details and trusted-proxy/Entra authentication

Approved on 2026-08-21:

- `docs/grc_gold_contract_additions.md` and its proposed version-one field names.
- `bridgeprojectsector` is the complete Project-sector set, including the primary sector exactly once.
- `factproject.organizationkey` is the reporting National Society organization.
- `factactivity.organizationkey` is the Activity lead organization.
- Organization, geography, sector and operation relationships are resolved through Gold keys/FKs, never names.
- `dimlocation` may contain ADM1, ADM2 and deeper levels. Non-ADM1 rows are valid but are not projected to GO District.
- Event and Project location bridges may reference ADM2/deeper rows; version 1 validates the references but projects only ADM1 rows.
- The consumed sync timestamps must be timezone-aware; the adapter rejects naive timestamps.
- A source ingestion timestamp later than its repeatable-read transaction watermark is invalid and is rejected.
- `grc_source_id` UUID is the cross-load Gold identity; Gold surrogate keys are relationship keys, not public GO IDs.
- Optional `gocountryid`, `godistrictid`, `goeventid`, `goprojectid`, and later `goappealid` preserve original GO targets only when they exist.
- Complete bridge semantics remove an absent relationship on the next successful snapshot; duplicate/null/unresolved associations fail the transaction.

## Current test deployment

The smoke-test deployment is running on Ubuntu VM `sr-ifrc`.

- SSH: `serveradmin@134.149.218.69`
- Internal application address: `http://172.20.250.103:8050/`
- Checkout root: `~/grc-go-test`
- API checkout: `~/grc-go-test/grc-go-api`
- Web checkout: `~/grc-go-test/grc-go-web-app`

Running containers:

| Container | Purpose | Published port |
|---|---|---|
| `grc-go-api-test` | Django development API | `0.0.0.0:8000 -> 8000` |
| `grc-go-web-test` | Built frontend served by web-app-serve | `0.0.0.0:8050 -> 80` |
| `grc-go-api-db-1` | PostGIS 15 | internal `5432` |
| `grc-go-api-redis-1` | Redis | internal `6379` |

Both application containers use restart policy `unless-stopped`. At handover, these checks returned HTTP 200:

```bash
curl 'http://172.20.250.103:8000/api/v2/country/?limit=1'
curl 'http://172.20.250.103:8050/'
```

The previous container `ifrcgo-go-web-app-web-app-serve-1` was stopped, not deleted, because it previously occupied public port 8050.

The untracked VM configuration files are:

- `~/grc-go-test/grc-go-api/.env`
- `~/grc-go-test/grc-go-web-app/web-app-serve/.env`

The web environment must contain:

```env
APP_API_ENDPOINT=http://172.20.250.103:8000/
APP_GRC_ENABLED_MODULES=country,project,operation,overview
```

The API environment uses `API_FQDN=http://172.20.250.103:8000` and `FRONTEND_URL=http://172.20.250.103:8050`.

For this Docker development-server smoke test, `DJANGO_READ_ONLY=false` is required. Setting it to true causes Django's startup migration introspection to be rejected by `django_read_only`, so `runserver` terminates. This is a deployment limitation, not a change in the product decision: a production serving process should be read-only, while the future publisher uses separate write access.

The VM is protected by a strict firewall, but the current deployment is plain HTTP, has no GRC authentication proxy, and uses Django's development server. It must not be treated as a production deployment.

## Updating the VM

API:

```bash
cd ~/grc-go-test/grc-go-api
git status --short
git pull --ff-only origin grc/read-model
docker compose build serve
DJANGO_READ_ONLY=false docker compose run --rm --no-deps migrate
docker rm -f grc-go-api-test
docker compose run -d --name grc-go-api-test --no-deps -p 8000:8000 serve
docker update --restart unless-stopped grc-go-api-test
```

Web:

```bash
cd ~/grc-go-test/grc-go-web-app
git status --short
git pull --ff-only origin grc/project-ui
docker compose -f web-app-serve/docker-compose.yml build web-app-serve
docker rm -f grc-go-web-test
docker compose -f web-app-serve/docker-compose.yml run -d --name grc-go-web-test --no-deps -p 8050:80 web-app-serve
docker update --restart unless-stopped grc-go-web-test
```

Database fixtures were loaded for this new test database. Do not rerun `loaddata` on a populated database without first checking its effects.

## Validation status

- The Gold-contract and resilience checkpoint documented here was developed on top of the API starting baseline above; use `git log -1` for its final commit identity.
- Changed Python files were previously syntax-parsed successfully.
- Web whitespace/static checks were previously completed.
- Docker images built successfully on the VM.
- Django migrations and base fixtures completed on the VM.
- API and Web smoke checks returned HTTP 200.
- Targeted reference and Project projection/orchestration/Gold-reader/management-command tests have been added but still require execution in a complete Django environment.
- Gold contract preflight and command tests have been added; the DDL template has not been applied to any database.
- Event and Project bridge tests cover valid ADM2/deeper rows, missing location-dimension targets, null administrative levels, and duplicate links.
- Sync status, structured log context, and watermark-boundary tests have been added.
- Full API and frontend automated test suites have not yet been run in a complete local development environment.
- No DWH rows are synchronized yet; Project and Operation screens therefore do not demonstrate real GRC data.

## Recommended next implementation sequence

1. Review and adapt `docs/grc_gold_contract_additions.sql` in the DWH deployment process; do not execute it from GO.
2. Add/backfill the approved GO identities and authoritative names in Gold.
3. Backfill immutable UUIDs and apply/review the approved Berlin-local to `timestamptz` conversion, including DST edge cases.
4. Provision read-only DWH credentials, run `grc_check_dwh_contract`, then validate `grc_sync_reference` against a non-production Gold database.
5. Validate `grc_sync_projects` against the same non-production Gold database after reference publication.
6. Backfill exact Appeal type IDs and the remaining Appeal identifiers/timestamps; then implement Operations with documented non-aggregate `0.0` financial placeholders. Activity mappings remain a separate approval.
7. Add scheduled execution, external metrics/alerts, quarantine reporting, and reference deletion reconciliation.
8. Replace the smoke-test deployment with a production WSGI/ASGI setup, reverse proxy, TLS, backups, and the trusted Entra authentication proxy.
9. Implement version-2 Country Profile and SharePoint enhancements separately.

Preserve the upstream API and page contracts during every phase. New GRC logic should remain isolated and should publish into existing GO models only when Gold and GO semantics genuinely match.
