-- GRC GO Gold-layer contract additions
--
-- Review template only: this file is not a GO/Django migration and is never
-- executed by the GO application. Apply it through the DWH deployment process
-- after backup, ownership, data-backfill, and rollback review.
--
-- New columns are intentionally nullable so existing rows can be backfilled
-- before the post-load contract checks are enforced. GO readers reject mapped
-- rows whose required values are still null.

BEGIN;

-- DWH-owned immutable source identities. These UUIDs identify Gold entities;
-- optional GO IDs below identify the existing GO cache row when one already
-- exists. GRC-only entities receive their GO integer ID during publication.
ALTER TABLE public.dimcountry ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.dimlocation ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.dimdisastertype ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.dimdisasterevent ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.dimorganization ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.dimoperationstatus ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.dimsector ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.dimactivitytype ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.dimmodality ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.dimdeliverymechanism ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.dimdonor ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.dimindicator ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.dimsex ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.dimagegroup ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.dimdisability ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.dimriskcategory ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.dimsafeguarding ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.dimyesnounknown ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.factproject ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.factoperation ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.factactivity ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.factfunding ADD COLUMN IF NOT EXISTS grc_source_id uuid;
ALTER TABLE public.factindicatorvalue ADD COLUMN IF NOT EXISTS grc_source_id uuid;

-- Country and geography identities required by the existing GO API contract.
ALTER TABLE public.dimcountry
    ADD COLUMN IF NOT EXISTS gocountryid integer,
    ADD COLUMN IF NOT EXISTS goregionid integer,
    ADD COLUMN IF NOT EXISTS goregionnameid smallint,
    ADD COLUMN IF NOT EXISTS gorecordtypeid smallint,
    ADD COLUMN IF NOT EXISTS independentflag boolean,
    ADD COLUMN IF NOT EXISTS sovereigncountrykey integer,
    ADD COLUMN IF NOT EXISTS societyname character varying(300),
    ADD COLUMN IF NOT EXISTS centroidlatitude numeric(9,6),
    ADD COLUMN IF NOT EXISTS centroidlongitude numeric(9,6),
    ADD COLUMN IF NOT EXISTS bboxwest numeric(9,6),
    ADD COLUMN IF NOT EXISTS bboxsouth numeric(9,6),
    ADD COLUMN IF NOT EXISTS bboxeast numeric(9,6),
    ADD COLUMN IF NOT EXISTS bboxnorth numeric(9,6),
    ADD COLUMN IF NOT EXISTS sourceupdatedat timestamp with time zone,
    ADD COLUMN IF NOT EXISTS ingestedat timestamp with time zone;

ALTER TABLE public.dimlocation
    ADD COLUMN IF NOT EXISTS godistrictid integer,
    ADD COLUMN IF NOT EXISTS sourceupdatedat timestamp with time zone,
    ADD COLUMN IF NOT EXISTS ingestedat timestamp with time zone;

ALTER TABLE public.dimdisastertype
    ADD COLUMN IF NOT EXISTS godisastertypeid integer;

ALTER TABLE public.dimdisasterevent
    ADD COLUMN IF NOT EXISTS goeventid integer,
    ADD COLUMN IF NOT EXISTS disasterstartat timestamp with time zone,
    ADD COLUMN IF NOT EXISTS peopleaffected integer,
    ADD COLUMN IF NOT EXISTS goifrcseveritylevelid smallint,
    ADD COLUMN IF NOT EXISTS ifrcseveritylevelupdatedat timestamp with time zone,
    ADD COLUMN IF NOT EXISTS sourceupdatedat timestamp with time zone,
    ADD COLUMN IF NOT EXISTS ingestedat timestamp with time zone;

CREATE TABLE IF NOT EXISTS public.bridgedisastereventcountry (
    disastereventkey integer NOT NULL,
    countrykey integer NOT NULL,
    isprimary boolean NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS public.bridgedisastereventlocation (
    disastereventkey integer NOT NULL,
    locationkey integer NOT NULL
);

-- Project identities and exact existing GO enum/reference domains.
ALTER TABLE public.factproject
    ADD COLUMN IF NOT EXISTS goprojectid integer,
    ADD COLUMN IF NOT EXISTS projectname character varying(500),
    ADD COLUMN IF NOT EXISTS goprojectprogrammetypeid smallint,
    ADD COLUMN IF NOT EXISTS goprojectoperationtypeid smallint;

ALTER TABLE public.dimsector
    ADD COLUMN IF NOT EXISTS goprojectprimarysectorid integer,
    ADD COLUMN IF NOT EXISTS goprojectsecondarysectortagid integer;

ALTER TABLE public.dimoperationstatus
    ADD COLUMN IF NOT EXISTS goprojectstatusid smallint,
    ADD COLUMN IF NOT EXISTS goappealstatusid smallint;

-- Approved future Operation and Activity source fields. Their GO publication
-- remains blocked by the mappings documented in grc_gold_contract_additions.md.
ALTER TABLE public.factoperation
    ADD COLUMN IF NOT EXISTS goappealid integer,
    ADD COLUMN IF NOT EXISTS goappealtypeid smallint,
    ADD COLUMN IF NOT EXISTS operationname character varying(500),
    ADD COLUMN IF NOT EXISTS operationcode character varying(50),
    ADD COLUMN IF NOT EXISTS operationtypecode character varying(50);

ALTER TABLE public.factactivity
    ADD COLUMN IF NOT EXISTS activityname character varying(500),
    ADD COLUMN IF NOT EXISTS activityleadtypecode character varying(30),
    ADD COLUMN IF NOT EXISTS activitydescription character varying(2000);

-- Stable GO identities are unique only when present. Null remains available
-- for Gold records that are deliberately outside the GO projection.
CREATE UNIQUE INDEX IF NOT EXISTS grc_dimcountry_source_id_uq
    ON public.dimcountry (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_dimlocation_source_id_uq
    ON public.dimlocation (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_dimdisastertype_source_id_uq
    ON public.dimdisastertype (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_dimdisasterevent_source_id_uq
    ON public.dimdisasterevent (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_dimorganization_source_id_uq
    ON public.dimorganization (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_dimoperationstatus_source_id_uq
    ON public.dimoperationstatus (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_dimsector_source_id_uq
    ON public.dimsector (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_dimactivitytype_source_id_uq
    ON public.dimactivitytype (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_dimmodality_source_id_uq
    ON public.dimmodality (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_dimdeliverymechanism_source_id_uq
    ON public.dimdeliverymechanism (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_dimdonor_source_id_uq
    ON public.dimdonor (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_dimindicator_source_id_uq
    ON public.dimindicator (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_dimsex_source_id_uq
    ON public.dimsex (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_dimagegroup_source_id_uq
    ON public.dimagegroup (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_dimdisability_source_id_uq
    ON public.dimdisability (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_dimriskcategory_source_id_uq
    ON public.dimriskcategory (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_dimsafeguarding_source_id_uq
    ON public.dimsafeguarding (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_dimyesnounknown_source_id_uq
    ON public.dimyesnounknown (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_factproject_source_id_uq
    ON public.factproject (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_factoperation_source_id_uq
    ON public.factoperation (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_factactivity_source_id_uq
    ON public.factactivity (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_factfunding_source_id_uq
    ON public.factfunding (grc_source_id) WHERE grc_source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS grc_factindicatorvalue_source_id_uq
    ON public.factindicatorvalue (grc_source_id) WHERE grc_source_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS grc_dimcountry_gocountryid_uq
    ON public.dimcountry (gocountryid)
    WHERE gocountryid IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS grc_dimlocation_godistrictid_uq
    ON public.dimlocation (godistrictid)
    WHERE godistrictid IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS grc_dimdisastertype_goid_uq
    ON public.dimdisastertype (godisastertypeid)
    WHERE godisastertypeid IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS grc_dimdisasterevent_goeventid_uq
    ON public.dimdisasterevent (goeventid)
    WHERE goeventid IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS grc_factproject_goprojectid_uq
    ON public.factproject (goprojectid)
    WHERE goprojectid IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS grc_factoperation_goappealid_uq
    ON public.factoperation (goappealid)
    WHERE goappealid IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS grc_dimsector_goprimaryid_uq
    ON public.dimsector (goprojectprimarysectorid)
    WHERE goprojectprimarysectorid IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS grc_dimsector_gosecondaryid_uq
    ON public.dimsector (goprojectsecondarysectortagid)
    WHERE goprojectsecondarysectortagid IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS grc_bdec_event_country_uq
    ON public.bridgedisastereventcountry (disastereventkey, countrykey);

CREATE UNIQUE INDEX IF NOT EXISTS grc_bdec_one_primary_uq
    ON public.bridgedisastereventcountry (disastereventkey)
    WHERE isprimary;

CREATE INDEX IF NOT EXISTS grc_bdec_country_idx
    ON public.bridgedisastereventcountry (countrykey);

CREATE UNIQUE INDEX IF NOT EXISTS grc_bdel_event_location_uq
    ON public.bridgedisastereventlocation (disastereventkey, locationkey);

CREATE INDEX IF NOT EXISTS grc_bdel_location_idx
    ON public.bridgedisastereventlocation (locationkey);

-- Existing Project bridges are part of the approved GO serving contract. The
-- unique indexes intentionally fail DWH review if duplicate relationships must
-- be cleaned before publication.
CREATE UNIQUE INDEX IF NOT EXISTS grc_bps_project_sector_uq
    ON public.bridgeprojectsector (projectid, sectorkey);

CREATE UNIQUE INDEX IF NOT EXISTS grc_bpl_project_location_uq
    ON public.bridgeprojectlocation (projectid, locationkey);

CREATE UNIQUE INDEX IF NOT EXISTS grc_bpo_one_primary_uq
    ON public.bridgeprojectoperation (projectid)
    WHERE isprimary;

-- Add FK constraints idempotently. NOT VALID permits a controlled backfill;
-- the DWH release must validate them before GO synchronization is enabled.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'grc_dimcountry_sovereign_fk'
          AND conrelid = 'public.dimcountry'::regclass
    ) THEN
        ALTER TABLE public.dimcountry
            ADD CONSTRAINT grc_dimcountry_sovereign_fk
            FOREIGN KEY (sovereigncountrykey)
            REFERENCES public.dimcountry(countrykey)
            NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'grc_bdec_event_fk'
          AND conrelid = 'public.bridgedisastereventcountry'::regclass
    ) THEN
        ALTER TABLE public.bridgedisastereventcountry
            ADD CONSTRAINT grc_bdec_event_fk
            FOREIGN KEY (disastereventkey)
            REFERENCES public.dimdisasterevent(disastereventkey)
            NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'grc_bdec_country_fk'
          AND conrelid = 'public.bridgedisastereventcountry'::regclass
    ) THEN
        ALTER TABLE public.bridgedisastereventcountry
            ADD CONSTRAINT grc_bdec_country_fk
            FOREIGN KEY (countrykey)
            REFERENCES public.dimcountry(countrykey)
            NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'grc_bdel_event_fk'
          AND conrelid = 'public.bridgedisastereventlocation'::regclass
    ) THEN
        ALTER TABLE public.bridgedisastereventlocation
            ADD CONSTRAINT grc_bdel_event_fk
            FOREIGN KEY (disastereventkey)
            REFERENCES public.dimdisasterevent(disastereventkey)
            NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'grc_bdel_location_fk'
          AND conrelid = 'public.bridgedisastereventlocation'::regclass
    ) THEN
        ALTER TABLE public.bridgedisastereventlocation
            ADD CONSTRAINT grc_bdel_location_fk
            FOREIGN KEY (locationkey)
            REFERENCES public.dimlocation(locationkey)
            NOT VALID;
    END IF;
END
$$;

-- Approved interpretation of existing naive ingestion timestamps: Berlin local
-- civil time. PostgreSQL uses the IANA name Europe/Berlin; Microsoft/Windows
-- systems use W. Europe Standard Time for the same deployment convention.
DO $$
DECLARE
    grc_table_name text;
BEGIN
    FOREACH grc_table_name IN ARRAY ARRAY[
        'factproject',
        'factoperation',
        'factactivity',
        'factfunding',
        'factindicatorvalue'
    ]
    LOOP
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = grc_table_name
              AND column_name = 'ingestedat'
              AND data_type = 'timestamp without time zone'
        ) THEN
            EXECUTE format(
                'ALTER TABLE public.%I ALTER COLUMN ingestedat '
                'TYPE timestamp with time zone '
                'USING ingestedat AT TIME ZONE %L',
                grc_table_name,
                'Europe/Berlin'
            );
        END IF;
    END LOOP;
END
$$;

COMMIT;

-- REQUIRED MANUAL FOLLOW-UP
--
-- Review ambiguous/nonexistent daylight-saving transition times before applying
-- the approved Europe/Berlin conversion, then run grc_check_dwh_contract.
-- After every entity UUID is backfilled and stable, set each grc_source_id
-- column NOT NULL through the DWH migration process. The partial unique indexes
-- above protect backfill uniqueness but intentionally permit staged nulls.
--
-- Validate the five NOT VALID foreign keys after their backfill has passed the
-- data-quality checks, for example:
-- ALTER TABLE public.dimcountry
--     VALIDATE CONSTRAINT grc_dimcountry_sovereign_fk;
