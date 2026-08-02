-- ===========================================================================
--  Niche 1 — Real estate: "sell them follow-up"
--
--  The product is not a CRM. Independent SA agencies already have Base,
--  Prop Data, Flex or a spreadsheet, and replacing that is a nine-month
--  political fight you will lose. The product is a follow-up engine that
--  sits beside whatever they have and refuses to let a lead go cold.
--
--  Three revenue mechanics this schema is built to prove:
--    1. Speed to first response on portal leads (Property24 / Private
--       Property), measured in seconds, not "same day".
--    2. Show-day register capture and the 90-day nurture that follows it.
--       This is the single largest un-captured lead source in SA residential.
--    3. FICA document collection. Estate agencies are accountable
--       institutions under FICA; chasing IDs and proof of address is
--       universally hated and perfectly automatable.
-- ===========================================================================

\set ON_ERROR_STOP on

CREATE TYPE realestate.lead_intent AS ENUM ('buyer', 'seller', 'tenant', 'landlord', 'unknown');
CREATE TYPE realestate.lead_stage AS ENUM (
  'new', 'contacted', 'qualified', 'viewing_booked', 'offer_submitted',
  'under_contract', 'transferred', 'lost', 'nurture'
);
CREATE TYPE realestate.mandate_type AS ENUM ('sole', 'open', 'joint', 'rental');

-- ---------------------------------------------------------------------------
-- Listings under mandate. Mandate expiry is a scheduled revenue event: a sole
-- mandate lapsing without a renewal conversation is money the principal never
-- sees, and almost nobody in the market watches the date.
-- ---------------------------------------------------------------------------
CREATE TABLE realestate.property (
  id              uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id       uuid NOT NULL REFERENCES agency.tenant(id) ON DELETE CASCADE,

  reference       text NOT NULL,             -- agency's own stock number
  portal_ids      jsonb NOT NULL DEFAULT '{}'::jsonb,  -- {"property24":"1163...", ...}

  street_address  text,
  suburb          text NOT NULL,
  city            text NOT NULL,
  province        text NOT NULL,
  asking_price_cents bigint NOT NULL,
  bedrooms        smallint,
  bathrooms       numeric(3,1),
  erf_size_sqm    integer,

  mandate         realestate.mandate_type NOT NULL DEFAULT 'open',
  mandate_start   date,
  mandate_expires date,
  listed_on       date NOT NULL DEFAULT current_date,
  agent_ref       text NOT NULL,             -- the agency's agent identifier
  status          text NOT NULL DEFAULT 'active',

  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, reference)
);

ALTER TABLE realestate.property
  ADD COLUMN asking_price_rands numeric(14,2)
  GENERATED ALWAYS AS (asking_price_cents / 100.0) VIRTUAL;

CREATE INDEX property_mandate_expiry ON realestate.property (tenant_id, mandate_expires)
  WHERE status = 'active' AND mandate = 'sole';
CREATE INDEX property_suburb ON realestate.property (tenant_id, suburb, asking_price_cents);

-- ---------------------------------------------------------------------------
-- Leads. One row per enquiry, deliberately NOT deduplicated against contact —
-- the same buyer enquiring on three listings is three pieces of intent, and
-- collapsing them destroys the strongest qualification signal you have.
-- ---------------------------------------------------------------------------
CREATE TABLE realestate.lead (
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id      uuid NOT NULL REFERENCES agency.tenant(id) ON DELETE CASCADE,
  contact_id     uuid NOT NULL REFERENCES core.contact(id) ON DELETE CASCADE,
  property_id    uuid REFERENCES realestate.property(id) ON DELETE SET NULL,

  intent         realestate.lead_intent NOT NULL DEFAULT 'unknown',
  stage          realestate.lead_stage  NOT NULL DEFAULT 'new',
  source         text NOT NULL,             -- 'property24','private_property','show_day',
                                            -- 'website','facebook','walk_in','referral'
  message        text,

  budget_min_cents bigint,
  budget_max_cents bigint,
  -- Pre-qualification state. SA buyers are overwhelmingly bond-dependent, so
  -- this single field predicts closure better than anything else captured.
  bond_status    text CHECK (bond_status IN
                   ('unknown','not_started','applied','pre_approved','cash','declined')),
  bond_originator text,                     -- 'ooba','betterbond','mortgagemax'

  assigned_agent_ref text,
  received_at    timestamptz NOT NULL DEFAULT now(),
  first_response_at timestamptz,
  last_touch_at  timestamptz,
  next_touch_due_at timestamptz,
  touch_count    integer NOT NULL DEFAULT 0,

  lost_reason    text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

-- THE number. Everything in the real-estate pitch reduces to this column:
-- portal leads that get a reply inside five minutes convert at a multiple of
-- those that wait for the agent to finish a show day. Stored in seconds so
-- the portal can chart a distribution rather than a vanity average.
ALTER TABLE realestate.lead
  ADD COLUMN response_seconds integer
  GENERATED ALWAYS AS (
    CASE WHEN first_response_at IS NOT NULL
         THEN GREATEST(0, (EXTRACT(EPOCH FROM (first_response_at - received_at)))::int)
    END
  ) VIRTUAL;

ALTER TABLE realestate.lead
  ADD COLUMN is_high_intent boolean
  GENERATED ALWAYS AS (
    bond_status IN ('pre_approved','cash') OR budget_max_cents >= 250000000
  ) VIRTUAL;

CREATE INDEX lead_due        ON realestate.lead (tenant_id, next_touch_due_at)
  WHERE stage NOT IN ('transferred','lost');
CREATE INDEX lead_unanswered ON realestate.lead (tenant_id, received_at)
  WHERE first_response_at IS NULL;
CREATE INDEX lead_stage      ON realestate.lead (tenant_id, stage, received_at DESC);

-- ---------------------------------------------------------------------------
-- Show days. The SA Sunday ritual, still run on a clipboard in most agencies.
-- Digitising the register (QR code on the gate → WhatsApp opt-in → nurture)
-- is the highest-yield single automation in this niche and the one that
-- demos best in a boardroom.
-- ---------------------------------------------------------------------------
CREATE TABLE realestate.show_day (
  id           uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id    uuid NOT NULL REFERENCES agency.tenant(id) ON DELETE CASCADE,
  property_id  uuid NOT NULL REFERENCES realestate.property(id) ON DELETE CASCADE,
  agent_ref    text NOT NULL,
  starts_at    timestamptz NOT NULL,
  ends_at      timestamptz NOT NULL,
  qr_token     text NOT NULL UNIQUE,       -- printed on the pavement board
  created_at   timestamptz NOT NULL DEFAULT now(),
  CHECK (ends_at > starts_at)
);

CREATE TABLE realestate.show_day_registration (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id     uuid NOT NULL REFERENCES agency.tenant(id) ON DELETE CASCADE,
  show_day_id   uuid NOT NULL REFERENCES realestate.show_day(id) ON DELETE CASCADE,
  contact_id    uuid NOT NULL REFERENCES core.contact(id) ON DELETE CASCADE,

  -- Captured on the digital register itself, which is also where the POPIA
  -- s69 consent wording lives. The consent row in core.consent references
  -- this registration id as its evidence.
  is_buying_now      boolean,
  has_property_to_sell boolean,
  timeframe          text,                 -- 'now','3_months','6_months','browsing'
  registered_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (show_day_id, contact_id)
);

CREATE INDEX show_day_reg_seller_signal ON realestate.show_day_registration
  (tenant_id, registered_at DESC) WHERE has_property_to_sell;

-- ---------------------------------------------------------------------------
-- Viewings. Confirmation + reminder + post-viewing feedback. The feedback
-- loop is what the seller actually wants and what most agencies never deliver,
-- which turns a buyer-side automation into a seller-retention tool.
-- ---------------------------------------------------------------------------
CREATE TABLE realestate.viewing (
  id           uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id    uuid NOT NULL REFERENCES agency.tenant(id) ON DELETE CASCADE,
  lead_id      uuid NOT NULL REFERENCES realestate.lead(id) ON DELETE CASCADE,
  property_id  uuid NOT NULL REFERENCES realestate.property(id) ON DELETE CASCADE,
  agent_ref    text NOT NULL,
  scheduled_for timestamptz NOT NULL,
  status       text NOT NULL DEFAULT 'scheduled'
    CHECK (status IN ('scheduled','confirmed','attended','no_show','cancelled')),
  feedback_score smallint CHECK (feedback_score BETWEEN 1 AND 5),
  feedback_text  text,
  feedback_shared_with_seller_at timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX viewing_upcoming ON realestate.viewing (tenant_id, scheduled_for)
  WHERE status IN ('scheduled','confirmed');

-- ---------------------------------------------------------------------------
-- FICA. Estate agencies are accountable institutions; incomplete customer
-- due diligence is a regulatory exposure the principal genuinely loses sleep
-- over. Automating the chase is boring, unglamorous, and closes deals.
-- ---------------------------------------------------------------------------
CREATE TABLE realestate.fica_request (
  id           uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id    uuid NOT NULL REFERENCES agency.tenant(id) ON DELETE CASCADE,
  contact_id   uuid NOT NULL REFERENCES core.contact(id) ON DELETE CASCADE,
  property_id  uuid REFERENCES realestate.property(id) ON DELETE SET NULL,
  party_role   text NOT NULL CHECK (party_role IN ('buyer','seller','tenant','landlord')),

  -- Which documents are outstanding. Documents themselves go to encrypted
  -- object storage; only the metadata and a checksum live in Postgres.
  required     text[] NOT NULL DEFAULT
                 ARRAY['id_document','proof_of_address','bank_confirmation'],
  received     jsonb NOT NULL DEFAULT '{}'::jsonb,
  reminders_sent integer NOT NULL DEFAULT 0,
  completed_at timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now()
);

-- A generated column's expression cannot contain a sub-select, so the key
-- count is wrapped in an IMMUTABLE helper.
--
-- This one is STORED rather than VIRTUAL: PostgreSQL 18 does not yet allow
-- virtual generated columns to call user-defined functions. Stored costs a
-- write, which is fine here — FICA requests are updated a handful of times
-- each, and the chase workflow reads outstanding_count on every pass.
CREATE OR REPLACE FUNCTION core.jsonb_object_size(j jsonb) RETURNS integer
  LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
AS $$ SELECT count(*)::int FROM jsonb_object_keys(j) $$;

ALTER TABLE realestate.fica_request
  ADD COLUMN outstanding_count integer
  GENERATED ALWAYS AS (
    cardinality(required) - core.jsonb_object_size(received)
  ) STORED;

CREATE INDEX fica_outstanding ON realestate.fica_request (tenant_id, created_at)
  WHERE completed_at IS NULL;

CREATE TRIGGER touch BEFORE UPDATE ON realestate.property
  FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();
CREATE TRIGGER touch BEFORE UPDATE ON realestate.lead
  FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

SELECT core.apply_tenant_rls(t) FROM unnest(ARRAY[
  'realestate.property'::regclass,
  'realestate.lead'::regclass,
  'realestate.show_day'::regclass,
  'realestate.show_day_registration'::regclass,
  'realestate.viewing'::regclass,
  'realestate.fica_request'::regclass
]) AS t;
