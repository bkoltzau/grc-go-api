# GRC GO Gold-Layer Contract Additions

Status: architecture input for a future DWH change; no database migration is implemented by this document.

## 1. Confirmed product and source decisions

- Reuse the retired upstream GO 3W Project list and detail experience as the Project UI baseline.
- Restore the retired read-only Country Project dashboard and Project list/detail capability initially. Project create/edit, local user workflows, global cross-country 3W analytics, and other removed write workflows are not part of the first restoration.
- Map `dimdisasterevent` to the GO Event/Emergency concept.
- Map `factoperation` to the GO Appeal/Operation concept.
- Funding is allocated to Projects, not directly to Operations.
- Detailed funding/financial data is a later DWH phase. Until then, operation requested/funded amounts are documented debt and must not be populated with zeroes or guessed Project values.
- Use the existing GO controlled values for Project/Activity indicators and disaggregation rather than inventing a GRC taxonomy.
- Keep the existing GO Country experience. There is no requirement for a new Country list page.
- Full Country NS/Profile/databank support is a version 2 capability.
- GO Country/reference data will first be ingested into Gold, where minor GRC corrections may be applied, and GO will then read the projected values back from Gold.
- The GO deployment is read-only for projected Country, Project, Operation, Activity, Funding, and Indicator records.
- Dimension and bridge change tracking still needs to be verified. Until confirmed, the sync design must support full comparison/hash processing for those tables.
- English is the only required UI language.
- SharePoint URLs and existing GO document metadata are sufficient for the initial document integration.

## 2. Historical Project UI contract

The retired frontend was removed by upstream commit `8d3a7bd69db74cf5c6151b6e88f570f6e2e5bd9c` (`Initiate shutdown for 3W`, 2025-04-29).

The historical Project list consumed `GET /api/v2/project/` and displayed:

- Project country
- Reporting National Society
- Project name/title
- Primary sector
- Budget amount
- Programme type
- Disaster type
- People targeted
- People reached

The historical Project detail consumed `GET /api/v2/project/{id}/` and displayed:

- Project name/title
- Last modification timestamp and optional modifying user
- Reporting National Society
- Project country
- Optional reporting contact name, role, and email
- Operation type
- Programme type
- Linked Event/Emergency
- Disaster type
- Primary and secondary sectors
- Start and end dates
- Status
- Optional annual budget/target/reached splits
- Otherwise, overall budget and sex-disaggregated target/reached values

The Project page did not require description or document fields for its visible read-only detail layout. Those fields remain valid future API capabilities but are not mandatory for the initial UI restoration.

The restored Country → Ongoing Activities → 3W Projects dashboard additionally consumes:

- `GET /api/v2/project/`, filtered by Country, for Project totals, status and programme charts, tables, and map points.
- `GET /api/v2/district/`, filtered by Country, for district names and centroids.
- `GET /api/v2/primarysector` and `GET /api/v2/secondarysector` for filter labels.
- The parent Country response for Country ID, name, ISO code, and bounding box.

This dashboard does not require another Gold fact or a new aggregate table. Project-to-district membership is a `bridgeprojectlocation` lookup, district names and coordinates are `dimlocation` lookups, sector labels are `dimsector` lookups, and Project status is a `dimoperationstatus` lookup. The scheduled projection may denormalize those values into the serving cache, but Gold should not duplicate them on `factproject` solely for this screen.

## 3. Mandatory version 1 Gold additions

Physical names should follow the final DWH naming convention. The logical field names below deliberately follow the existing lower-case Gold style.

### 3.1 `dimcountry`

GO reference data should be ingested into `dimcountry`, then exposed back to GO from Gold.

| Logical field | Suggested type | Required | Meaning and rule |
|---|---|---:|---|
| `gocountryid` | integer | Yes | Stable upstream GO Country ID. Must be unique for GO-sourced country records. |
| `goregionid` | integer | Yes | Stable upstream GO Region ID used by Country routes, filters, and breadcrumbs. |
| `goregionnameid` | smallint | Yes | Exact upstream `Region.name` enum value (currently 0-4). This is distinct from the Region database ID and avoids deriving the enum from a translated label. |
| `gorecordtypeid` | smallint | Yes | Exact upstream `Country.record_type` enum value (currently 1-5). Copy the GO value rather than introducing a textual mapping. |
| `independentflag` | boolean | Yes | GO independence value; must not be derived from `isactive`. |
| `sovereigncountrykey` | integer, nullable FK to `dimcountry` | Conditional | Required when GO identifies a different sovereign state. |
| `societyname` | varchar(300), nullable | Conditional | National Society display name used by current Country and 3W screens. |
| `centroidlatitude` | numeric(9,6), nullable | Yes for mapped countries | GO-compatible country centroid latitude. |
| `centroidlongitude` | numeric(9,6), nullable | Yes for mapped countries | GO-compatible country centroid longitude. |
| `bboxwest` | numeric(9,6), nullable | Yes for mapped countries | Western extent used to derive the GO bbox polygon. |
| `bboxsouth` | numeric(9,6), nullable | Yes for mapped countries | Southern extent used to derive the GO bbox polygon. |
| `bboxeast` | numeric(9,6), nullable | Yes for mapped countries | Eastern extent used to derive the GO bbox polygon. |
| `bboxnorth` | numeric(9,6), nullable | Yes for mapped countries | Northern extent used to derive the GO bbox polygon. |
| `sourceupdatedat` | timestamptz, nullable | Recommended | Last modification timestamp reported by GO. |
| `ingestedat` | timestamptz | Recommended | Gold ingestion timestamp for reference-data synchronization. |

The existing `region` column remains the human-readable region name. A distinct GO Region read model can be deterministically produced from `goregionid`, `goregionnameid`, and `region`; a new region dimension is not required for version 1 unless additional region attributes are needed.

### 3.2 `dimlocation` for GO District/ADM1

Only confirmed ADM1 rows are projected into GO `District`. Other `adminlevel`
values remain in Gold and must not be coerced into District records.

| Logical field | Suggested type | Required | Meaning and rule |
|---|---|---:|---|
| `godistrictid` | integer, nullable | Yes for mapped ADM1 | Stable upstream GO District ID. It must be unique for GO-mapped ADM1 rows. `locationkey` cannot replace it because GO map boundaries and API relations use the GO ID. |
| `sourceupdatedat` | timestamptz, nullable | Recommended | Last modification timestamp reported by GO for the location record. |
| `ingestedat` | timestamptz, nullable | Recommended | Gold ingestion timestamp for location synchronization. |

For mapped ADM1 rows, existing `pcode` is the GO District `code` and is required
to be non-empty and no longer than the upstream 10-character limit. Existing
`name` must fit GO's 100-character District limit. Existing `latitude` and
`longitude` are required because the Country Project map plots Project counts at
the District centroid. `countrykey` is joined to `dimcountry.gocountryid` before
publication.

No District bbox field is required for version 1: the restored page uses the
Country bbox for viewport bounds, Gold latitude/longitude for Project markers,
and the existing GO/Mapbox administrative boundary layer for polygons. GO-only
District attributes such as `is_enclave`, population, NUTS, EMMA, and FIPS are
not required and are preserved if already present in the serving cache.

### 3.2.1 `dimdisastertype` GO identity

| Logical field | Suggested type | Required | Meaning and rule |
|---|---|---:|---|
| `godisastertypeid` | integer, nullable | Yes for GO-mapped types | Exact existing GO `api.DisasterType` ID. Do not match or create types by translated name. |

The initial adapter validates this identity against GO's existing disaster-type
reference rows. It does not create or rename disaster types because Gold does
not contain the required GO `summary` field and the approved decision is to use
the existing GO controlled values.

### 3.2.2 `dimdisasterevent` and Event geography

| Logical field | Suggested type | Required | Meaning and rule |
|---|---|---:|---|
| `goeventid` | integer, nullable | Yes for mapped Events | Stable existing GO Event ID used by Project/Operation links and frontend routes. |
| `disasterstartat` | timestamptz, nullable | Yes for mapped Events | Original GO Event start timestamp. `startdatekey` remains the analytical date but cannot preserve the required timestamp alone. |
| `peopleaffected` | integer, nullable | Optional | Event-level affected-person count exposed as GO `num_affected`. Do not substitute Operation targeted/reached values. |
| `goifrcseveritylevelid` | smallint, nullable | Yes for mapped Events | Exact upstream GO `AlertLevel` value (currently 0-2). Zero is a valid value. |
| `ifrcseveritylevelupdatedat` | timestamptz, nullable | Optional | Timestamp associated with the severity value. |
| `sourceupdatedat` | timestamptz, nullable | Recommended | Last modification timestamp reported by GO. |
| `ingestedat` | timestamptz, nullable | Recommended | Gold ingestion timestamp for Event synchronization. |

For mapped Events, existing `name` becomes mandatory and fits GO's
256-character limit. Existing `description` maps directly to GO `summary`.
Existing `glide` must fit GO's shorter 18-character limit; null may be projected
as GO's supported blank value. A Project/Operation affected-person value is not
a valid substitute for `peopleaffected`.

The current `primarycountrykey` cannot represent GO Events covering multiple
countries, while the Event list, filters, detail header, and map consume the
complete country collection. Add this bridge:

| New table | Column | Rule |
|---|---|---|
| `bridgedisastereventcountry` | `disastereventkey` | FK to `dimdisasterevent`; part of the unique key. |
| `bridgedisastereventcountry` | `countrykey` | FK to `dimcountry`; part of the unique key. |
| `bridgedisastereventcountry` | `isprimary` | Exactly one true row, matching `dimdisasterevent.primarycountrykey`. |

The Event map also consumes GO District/ADM1 records. Add this bridge rather
than storing repeated location columns on the Event dimension:

| New table | Column | Rule |
|---|---|---|
| `bridgedisastereventlocation` | `disastereventkey` | FK to `dimdisasterevent`; part of the unique key. |
| `bridgedisastereventlocation` | `locationkey` | FK to `dimlocation`; part of the unique key. Version 1 projection accepts only confirmed ADM1 rows with `godistrictid`. |

GO Event regions are DERIVED from the projected countries' GO Regions.
`countries_for_preview` initially uses the same authoritative country set.
Events are published with upstream `MEMBERSHIP` visibility: authenticated users
can read them while anonymous requests cannot. This reuses upstream visibility
behavior and does not implement the future trusted-proxy authentication adapter.

An inactive Event cannot be represented by an upstream deprecation field. The
active-row publisher therefore rejects `isactive = false`; the later sync
orchestrator must reconcile those records through an explicit deletion policy
without corrupting the last valid read model.

### 3.3 `factproject`

| Logical field | Suggested type | Required | Meaning and rule |
|---|---|---:|---|
| `projectname` | varchar(500) | Yes | Authoritative Project title shown on Project list/detail pages. Must not be derived from sector, organization, operation, or source-system name. |
| `goprojectprogrammetypeid` | smallint | Yes | Exact upstream GO `ProgrammeTypes` integer value used to populate `programme_type` (currently 0-2). Do not map translated labels. |
| `goprojectoperationtypeid` | smallint | Yes | Exact upstream GO `OperationTypes` integer value used to populate `operation_type` (currently 0-1). This is separate from disaster type and operation status. |

No new Project date, country, budget, total target/reached, status, sector, location, organization, or operation-link fields are required: these already exist on `factproject` or its bridges/dimensions.

The following optional source fields should be added only if the initial restored detail must display them:

| Logical field | Suggested type | Reason |
|---|---|---|
| `reportingcontactname` | varchar(255), nullable | Historical detail conditionally displays it. |
| `reportingcontactrole` | varchar(255), nullable | Historical detail conditionally displays it. |
| `reportingcontactemail` | varchar(255), nullable | Historical detail conditionally displays it. |

Project `modified_at` can be derived from the latest `ingestedat`. `modified_by` should remain absent for DWH-published rows rather than naming a fictitious user.

Annual splits are optional in the historical detail UI. Gold can initially use overall `budgetamountchf`, `peopletargeted`, and `peoplereached`; no annual-split structure is required for version 1.

For GO-sourced rows, existing `projectid` is the stable upstream GO Project ID
and must retain that meaning after Gold correction and republishing. If another
source is added later, its identifier must not be placed in `projectid` unless
the DWH first assigns a collision-free persistent GO target identity.

### 3.3.1 `dimsector` GO Project identities

GO uses separate reference tables for a Project's required primary sector and
its optional secondary-sector tags. They happen to share some current numeric
IDs and labels, but they are not the same foreign-key domain.

| Logical field | Suggested type | Required | Meaning and rule |
|---|---|---:|---|
| `goprojectprimarysectorid` | integer, nullable | Yes when used by `factproject.sectorkey` | Exact existing GO `deployments.Sector` ID. Zero is a valid GO ID. |
| `goprojectsecondarysectortagid` | integer, nullable | Conditional | Exact existing GO `deployments.SectorTag` ID. Required only when the row is approved for use as a secondary Project sector. Zero is valid. |

The GO IDs, rather than the English label, are the cross-system identities.
The initial publisher reuses the existing GO reference rows and rejects an ID
that does not exist; it does not create a new taxonomy or infer identity by
matching `name`.

Existing `factproject.sectorkey` is the primary-sector relationship and resolves
through `goprojectprimarysectorid`. Do not publish `bridgeprojectsector` into
GO `secondary_sectors` until the bridge is confirmed to mean secondary sectors
rather than all Project-sector associations. Once confirmed, each bridge row
resolves through `goprojectsecondarysectortagid`; a null mapping is an error for
that use.

### 3.3.2 `dimoperationstatus` GO identities

Project and Appeal/Operation statuses are different GO enum domains. For
example, integer zero currently means `Planned` for a Project but `Active` for
an Appeal. One generic numeric mapping would therefore be invalid.

| Logical field | Suggested type | Required | Meaning and rule |
|---|---|---:|---|
| `goprojectstatusid` | smallint, nullable | Yes for Project statuses | Exact upstream GO `deployments.Statuses` integer value (currently 0-2). |
| `goappealstatusid` | smallint, nullable | Yes for published Operations | Exact upstream GO `api.AppealStatus` integer value (currently 0-3). It must be approved independently from the Project mapping. |

The Project publisher must also verify that `goprojectstatusid` agrees with the
status upstream GO derives from the Project start and end dates on the
publication date. A conflicting row is quarantined; the adapter must not
silently prefer either value.

### 3.4 `factoperation`

| Logical field | Suggested type | Required | Meaning and rule |
|---|---|---:|---|
| `operationname` | varchar(500) | Yes | Authoritative Operation/Appeal title. It is not the Disaster Event name. |
| `operationcode` | varchar(50) | Yes | Stable human-readable operational code used by GO tables and document references. |
| `operationtypecode` | varchar(50) | Yes | Controlled operation/appeal type. It is separate from disaster type and operation status. |

The existing `disastereventkey` links the Operation to the GO Event/Emergency projection. Existing start/end dates, country, disaster type, status, people targeted, and people reached remain authoritative.

Do not add or populate operation `amountrequestedchf` or `amountfundedchf` in version 1. Funding is Project-owned and the financial model is deferred. GO operation funding widgets cannot be truthfully populated until that debt is resolved.

### 3.5 `factactivity`

| Logical field | Suggested type | Required | Meaning and rule |
|---|---|---:|---|
| `activityname` | varchar(500) | Yes | Authoritative title for the GO 3W Activity envelope. It must not be copied from `dimactivitytype.name`. |
| `activityleadtypecode` | varchar(30) | Yes | GO-controlled lead type, initially matching the GO values such as National Society or deployed ERU where applicable. |
| `activitydescription` | varchar(2000), nullable | Recommended | Narrative/details displayed for a nested Activity when supplied by the source. |

`factactivity.organizationkey` should be formally defined as the primary/lead organization if that is its intended meaning. If it currently means something broader, add a separate nullable `activityleadorganizationkey` FK or identify the lead through `bridgeactivityorganizationrole` with a mandatory controlled role code.

No new date, Project/Operation relation, activity type, sector, status, modality, delivery mechanism, or location fields are required.

### 3.6 Organization and role semantics

No guessed mapping from a generic organization to a GO National Society or ERU is permitted.

Gold must publish controlled meanings for:

- `dimorganization.orgtype`
- `bridgeprojectorganizationrole.rolecode`
- `bridgeactivityorganizationrole.rolecode`

At minimum, the role vocabulary must distinguish:

- Project reporting organization/National Society
- Project implementing/partner organization
- Activity lead organization
- Activity implementing/supporting organization
- Donor, where represented outside `dimdonor`

For a National Society mapping, the selected organization must have an authoritative `countrykey` and an organization type that explicitly means National Society. An organization name containing words such as “Red Cross” is not sufficient.

For a deployed ERU mapping, Gold must identify the specific deployed ERU record and its owning National Society. A generic organization row is insufficient because GO exposes ERU deployment details separately.

If GRC version 1 never contains deployed-ERU-led Activities, Gold should state that as a source contract and publish only the National Society lead type. The adapter must still reject, rather than guess, an unsupported lead type.

## 4. Use of existing Gold fields and GO controlled values

### 4.1 Project disaggregation

The historical Project detail can derive sex-disaggregated target/reached values from `factindicatorvalue` only when Gold publishes canonical GO-compatible rows.

The DWH mapping must define which existing GO measure is represented and whether `targetvalue` or `actualvalue` supplies each output. It must also define a non-double-counting canonical grain. For example, Project totals must not sum both total rows and their age/location breakdown rows.

If canonical rows are not available in version 1, overall `factproject.peopletargeted` and `factproject.peoplereached` remain valid, while the sex-disaggregated fields remain unavailable.

### 4.2 Activity indicators

Use the actual GO 3W field definitions and buckets as the controlled contract. Required output concepts include, where source data exists:

- People versus households
- Overall people/beneficiary count
- Male, female, and other/unknown
- GO age buckets
- Disability disaggregation
- Household count
- Item count
- Cash amount

Implement these through `dimindicator`, `dimsex`, `dimagegroup`, `dimdisability`, and `factindicatorvalue`. Do not create semantically similar substitute codes.

Before implementation, create an approved mapping registry containing:

- GO output field name
- `indicatorkey`
- applicable `sexkey`, `agegroupkey`, and `disabilitykey`
- target versus actual value
- cumulative handling
- aggregation grain and deduplication rule

This registry may initially be configuration owned by `grc_sync`; it does not require a new Gold table unless the DWH team wants it to be data-managed.

### 4.3 Country/Event reference values

The following values can already be sourced without new fact columns:

- Event name and description: `dimdisasterevent`
- Event GLIDE and disaster type: `dimdisasterevent`
- Event primary country: `dimdisasterevent.primarycountrykey`
- Project/Operation/Activity dates: the corresponding date keys joined to `dimdate`
- Countries, sectors, organizations, statuses, locations, disaster types, activity types, modalities, and delivery mechanisms: existing dimensions and bridges

## 5. Documented debt and version 2 scope

### 5.1 Project funding and financial data

Status: deferred to the next DWH financial phase.

- Funding remains allocated to Projects through `factfunding`.
- Do not infer Project expenditure from received funding.
- Do not infer Operation requested/funded totals from Project budget or funding.
- If future Operation rollups are required, the DWH phase must define allocation semantics for `bridgeprojectoperation.allocationshare`, including null shares and Projects linked to multiple Operations.
- Until then, operation funding fields are unavailable, not zero.

### 5.2 Country Profile/databank

Status: version 2.

The existing Country Profile consumes World Bank, HDR, ACAPS, climate, FDRS, National Society, capacity, directory, initiative, supporting-partner, contact, and document data. Version 2 should ingest the relevant GO reference data into Gold before GO consumes it. The storage shape should be designed from the actual profile endpoint contract rather than adding all fields speculatively to `dimcountry`.

### 5.3 Dimension and bridge change tracking

Status: verification required.

Facts have `ingestedat` and `isdeleted`; current dimensions and bridges generally do not. Confirm whether the DWH platform supplies CDC or reliable source-updated timestamps outside this schema.

If it does not, choose one of:

1. Add `ingestedat` and `isdeleted`/active-change metadata to all dimensions and bridges used by GO; or
2. Perform a full key-and-content-hash comparison for those relatively stable tables during every two-hour sync.

The selected rule must be documented before incremental synchronization is implemented.

## 6. Publication and validation rules

- Project rows without `projectname` must be quarantined and not published to GO.
- Country `name` values longer than the existing GO maximum of 100 characters must be quarantined or corrected in Gold; the adapter must not truncate them.
- Country ISO2/ISO3 values must be uppercase and unique, GO Region/Country-type enum IDs must resolve exactly, and mapped Country rows require complete valid centroid/bounding-box values.
- Project rows without a resolved reporting National Society, Country, primary sector, start date, or end date must be quarantined because the existing GO Project model requires them. Gold currently permits nulls for several of these keys.
- The mapped Gold Project status must agree with GO's existing date-based Project status rule. A mismatch is a validation error, not permission to silently replace either value.
- Operation rows without `operationname`, `operationcode`, or a valid operation type must be quarantined and not published as Appeals.
- Activity rows without `activityname`, a valid lead type, an unambiguous lead organization mapping, a start date, Country, or required Event/Operation relationship must be quarantined and not published.
- Event rows may use `dimdisasterevent.name`; Operation rows may not reuse it as their own title.
- Missing numeric funding values must not be converted to zero.
- Unknown enum/code mappings must fail validation and appear in sync metrics.
- All source-to-GO identifiers must be stable across idempotent reruns.
- Publication should occur in one transaction so a failed sync leaves the previously valid GO read model visible.

### 6.1 Selected serving-cache boundary

For fields with equivalent semantics, the existing GO ORM tables are the serving cache. Country, District, Event, Project, Operation/Appeal, and Activity API viewsets continue to read their existing querysets and use their existing serializers. This avoids a parallel API model and preserves filters, pagination, permissions, and response properties.

The isolated `grc_read_model` Django app stores only publication metadata:

- Stable Gold-to-GO source identities and target object IDs
- Source ingestion timestamps, content hashes, and soft-delete state
- Publication-run status, counters, and watermark ranges
- The last successfully published watermark per source stream

The serving API should use upstream's existing `DJANGO_READ_ONLY=true` configuration. The future scheduled publisher runs separately with write access and advances a watermark only in the same successful transaction as its GO cache updates. Semantic mismatches, currently including unavailable Operation funding values, still require an isolated adapter or a completed Gold source; the metadata layer must not manufacture compatible-looking values.

## 7. Remaining data-contract decisions

- Confirm the Operation/Appeal type mapping. Project programme type and Project operation type now use exact upstream GO integer IDs in Gold and require no textual mapping.
- Confirm that `bridgeprojectsector` contains secondary-sector associations rather than a complete set that also repeats the primary sector.
- Confirm whether `factactivity.organizationkey` already means activity lead.
- Confirm whether deployed ERUs occur in the GRC source data and how they are represented.
- Extract and approve the exact historical GO disaggregation vocabulary and aggregation rules.
- Confirm the DWH platform’s dimension/bridge CDC capability.
- Confirm the timezone convention for existing `timestamp without time zone` ingestion columns. New GO reference-data timestamps should use `timestamptz`; the adapter must not assume that an existing naive timestamp is UTC.
- Confirm that `dimlocation.adminlevel = 1` is the authoritative ADM1 convention for GO-sourced rows before enabling District publication. The adapter deliberately rejects every other level.
- Confirm that Event country/location bridges are populated as complete snapshots, including multi-country Events and every ADM1 location required by the existing Event map.
