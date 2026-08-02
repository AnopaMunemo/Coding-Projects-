-- ===========================================================================
--  Core multi-tenant schema — PostgreSQL 18
--
--  Design rules that everything downstream depends on:
--
--   1. ONE cluster, ONE database, MANY tenants. Isolation is enforced by
--      row-level security keyed on a session GUC (app.tenant_id), not by
--      spinning up a stack per client. n8n sets the GUC on every connection.
--   2. Every table carries tenant_id and is FORCE ROW LEVEL SECURITY, so even
--      the owning role cannot read across tenants by accident.
--   3. Primary keys are uuidv7() — new in PG18. Time-ordered UUIDs give you
--      random-key privacy with sequential-key index locality, which matters
--      once the event table is in the tens of millions of rows.
--   4. Consent and suppression are first-class tables, not columns. POPIA
--      s69 compliance has to be provable per message, per channel, per date.
--
--  Applied automatically on first boot via /docker-entrypoint-initdb.d.
-- ===========================================================================

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;           -- gen_random_bytes, digest
CREATE EXTENSION IF NOT EXISTS btree_gist;         -- equality ops in EXCLUDE
CREATE EXTENSION IF NOT EXISTS citext;             -- case-insensitive email
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS pg_trgm;            -- fuzzy dedupe on names

-- ---------------------------------------------------------------------------
-- Schemas
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS agency;      -- your business: tenants, contracts
CREATE SCHEMA IF NOT EXISTS core;        -- shared: contacts, consent, messages
CREATE SCHEMA IF NOT EXISTS realestate;  -- niche 1
CREATE SCHEMA IF NOT EXISTS medspa;      -- niche 2
CREATE SCHEMA IF NOT EXISTS analytics;   -- read models for the client portal
CREATE SCHEMA IF NOT EXISTS n8n;         -- n8n's own tables, kept separate

-- ---------------------------------------------------------------------------
-- Roles
--   agency_app        read/write, used by n8n + the Python gateway
--   agency_portal_ro  read-only, used by the Next.js client portal
--   Neither is a superuser, so RLS actually applies to them.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agency_app') THEN
    CREATE ROLE agency_app LOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agency_portal_ro') THEN
    CREATE ROLE agency_portal_ro LOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'n8n_app') THEN
    CREATE ROLE n8n_app LOGIN;
  END IF;
END
$$;

GRANT USAGE ON SCHEMA agency, core, realestate, medspa, analytics TO agency_app;
GRANT USAGE ON SCHEMA analytics TO agency_portal_ro;
GRANT ALL   ON SCHEMA n8n TO n8n_app;

-- ---------------------------------------------------------------------------
-- Tenant resolution
--
-- n8n / the gateway open a connection and immediately run:
--     SELECT set_config('app.tenant_id', $1, true);
-- The `true` makes it transaction-local, so a pooled connection can never
-- leak a tenant context into the next execution.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.current_tenant() RETURNS uuid
  LANGUAGE sql STABLE PARALLEL SAFE
AS $$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$;

COMMENT ON FUNCTION core.current_tenant() IS
  'Returns the tenant scoping the current transaction. NULL means no tenant '
  'context was set, which every RLS policy treats as "see nothing".';

-- ===========================================================================
--  agency.*  — your own book of business
-- ===========================================================================

CREATE TYPE agency.niche AS ENUM ('real_estate', 'med_spa');
CREATE TYPE agency.plan_tier AS ENUM ('foundation', 'growth', 'performance');
CREATE TYPE agency.tenant_status AS ENUM
  ('prospect', 'pilot', 'active', 'paused', 'churned');

CREATE TABLE agency.tenant (
  id                uuid PRIMARY KEY DEFAULT uuidv7(),
  slug              citext NOT NULL UNIQUE,
  legal_name        text   NOT NULL,
  trading_name      text   NOT NULL,
  niche             agency.niche NOT NULL,
  status            agency.tenant_status NOT NULL DEFAULT 'prospect',
  plan_tier         agency.plan_tier,

  -- Commercials, in rands. Quote in ZAR; the USD band in the README is the
  -- positioning reference, not the invoice.
  setup_fee_cents       bigint,
  monthly_retainer_cents bigint,
  contract_start        date,
  contract_months       int DEFAULT 6,
  escalation_pct        numeric(4,2) DEFAULT 8.00,  -- annual, CPI + margin

  -- POPIA. You are an Operator (s21) for every tenant; the signed agreement
  -- and the client's own Information Officer are recorded here.
  operator_agreement_signed_on date,
  information_officer_name     text,
  information_officer_email    citext,

  timezone          text NOT NULL DEFAULT 'Africa/Johannesburg',
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),

  -- A live tenant must have a signed operator agreement. Non-negotiable:
  -- this is the clause that keeps a client's data breach from becoming yours.
  CONSTRAINT tenant_operator_agreement_required
    CHECK (status NOT IN ('active','pilot') OR operator_agreement_signed_on IS NOT NULL)
);

-- Monthly recurring revenue in rands, computed on read rather than stored.
-- PG18 VIRTUAL generated columns cost nothing on write and cannot drift.
ALTER TABLE agency.tenant
  ADD COLUMN mrr_rands numeric(12,2)
  GENERATED ALWAYS AS (monthly_retainer_cents / 100.0) VIRTUAL;

CREATE TABLE agency.integration (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id     uuid NOT NULL REFERENCES agency.tenant(id) ON DELETE CASCADE,
  provider      text NOT NULL,          -- 'meta_whatsapp','google_calendar','yoco',...
  external_ref  text,                   -- phone_number_id, calendar_id, merchant_id
  -- Secrets live in n8n's encrypted credential store, NOT here. This table
  -- only records which credential to use, so a dump of the database never
  -- yields a working token.
  n8n_credential_id text,
  config        jsonb NOT NULL DEFAULT '{}'::jsonb,
  is_active     boolean NOT NULL DEFAULT true,
  last_verified_at timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, provider, external_ref)
);

-- ===========================================================================
--  core.*  — shared entities, one row set per tenant
-- ===========================================================================

CREATE TYPE core.channel AS ENUM ('whatsapp', 'sms', 'email', 'voice', 'in_person');
CREATE TYPE core.direction AS ENUM ('inbound', 'outbound');
CREATE TYPE core.consent_basis AS ENUM (
  'express_consent',      -- POPIA s69(1)(a): Form 4-equivalent capture
  'existing_customer',    -- POPIA s69(3): the soft opt-in
  'legitimate_service'    -- transactional/utility, not direct marketing
);

CREATE TABLE core.contact (
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id      uuid NOT NULL REFERENCES agency.tenant(id) ON DELETE CASCADE,

  first_name     text,
  last_name      text,
  -- E.164, always. SA numbers normalise to +27XXXXXXXXX before insert; the
  -- gateway rejects anything else so WhatsApp lookups never silently miss.
  msisdn         text,
  email          citext,

  source         text,          -- 'property24','show_day','instagram','referral'
  source_detail  jsonb NOT NULL DEFAULT '{}'::jsonb,
  owner_user_ref text,          -- the agent / practitioner who owns the relationship

  first_seen_at  timestamptz NOT NULL DEFAULT now(),
  last_activity_at timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT contact_msisdn_e164 CHECK (msisdn IS NULL OR msisdn ~ '^\+[1-9][0-9]{7,14}$'),
  CONSTRAINT contact_reachable   CHECK (msisdn IS NOT NULL OR email IS NOT NULL)
);

ALTER TABLE core.contact
  ADD COLUMN full_name text
  GENERATED ALWAYS AS (btrim(coalesce(first_name,'') || ' ' || coalesce(last_name,''))) VIRTUAL;

-- One person, one row, per tenant — matched on whichever identifier exists.
CREATE UNIQUE INDEX contact_tenant_msisdn_uk ON core.contact (tenant_id, msisdn)
  WHERE msisdn IS NOT NULL;
CREATE UNIQUE INDEX contact_tenant_email_uk  ON core.contact (tenant_id, email)
  WHERE email IS NOT NULL;
CREATE INDEX contact_name_trgm ON core.contact USING gin (
  (coalesce(first_name,'') || ' ' || coalesce(last_name,'')) gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- Consent ledger. Append-only. Never UPDATE a consent row — withdrawal is a
-- new row with granted = false. When the Information Regulator or a client's
-- attorney asks "on what basis did you WhatsApp my client on 14 March", this
-- table is the answer, and it has to be defensible without commentary.
-- ---------------------------------------------------------------------------
CREATE TABLE core.consent (
  id           uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id    uuid NOT NULL REFERENCES agency.tenant(id) ON DELETE CASCADE,
  contact_id   uuid NOT NULL REFERENCES core.contact(id) ON DELETE CASCADE,
  channel      core.channel NOT NULL,
  purpose      text NOT NULL,            -- 'direct_marketing','appointment_reminder'
  basis        core.consent_basis NOT NULL,
  granted      boolean NOT NULL,
  -- Evidence: the exact wording shown, where, and the request metadata.
  evidence     jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- clock_timestamp(), NOT now(). now() returns the transaction start time, so
  -- a grant and a withdrawal written in the same transaction would carry an
  -- identical timestamp and the ledger could not order them. That is a
  -- compliance failure, not a cosmetic one.
  captured_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
  expires_at   timestamptz
);

CREATE INDEX consent_lookup ON core.consent
  (tenant_id, contact_id, channel, purpose, captured_at DESC, id DESC);

-- Latest state per (contact, channel, purpose). n8n checks this view — never
-- the base table — before any outbound marketing send.
--
-- The `id DESC` tiebreak is load-bearing: uuidv7 is time-ordered, so if two
-- rows ever do share a timestamp, the later insert still wins. Without it,
-- DISTINCT ON resolves the tie arbitrarily and a withdrawal can silently lose
-- to the grant it was meant to revoke.
CREATE VIEW core.consent_current AS
SELECT DISTINCT ON (tenant_id, contact_id, channel, purpose)
       tenant_id, contact_id, channel, purpose, basis, granted, captured_at, expires_at
FROM   core.consent
ORDER  BY tenant_id, contact_id, channel, purpose, captured_at DESC, id DESC;

-- Hard suppression. Survives re-import, deduplication and CRM migration,
-- because it is keyed on the identifier rather than the contact row.
CREATE TABLE core.suppression (
  tenant_id   uuid NOT NULL REFERENCES agency.tenant(id) ON DELETE CASCADE,
  identifier  text NOT NULL,             -- msisdn or lowercased email
  channel     core.channel NOT NULL,
  reason      text NOT NULL,             -- 'opt_out','complaint','bounce','registry'
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, identifier, channel)
);

-- ---------------------------------------------------------------------------
-- Message log. Every send and receive, whatever the channel. This is what
-- makes the monthly report defensible and what makes debugging a 22:00
-- "my clients aren't getting messages" call take four minutes instead of two
-- hours.
-- ---------------------------------------------------------------------------
CREATE TABLE core.message (
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id      uuid NOT NULL REFERENCES agency.tenant(id) ON DELETE CASCADE,
  contact_id     uuid REFERENCES core.contact(id) ON DELETE SET NULL,
  channel        core.channel NOT NULL,
  direction      core.direction NOT NULL,

  template_name  text,                   -- Meta-approved template, if templated
  body           text,
  -- 'marketing' | 'utility' | 'authentication' | 'service'. Drives cost and,
  -- more importantly, drives whether a consent check is required.
  meta_category  text,

  provider       text,                   -- 'meta_cloud','clickatell','ses'
  provider_msg_id text,
  status         text NOT NULL DEFAULT 'queued',
  cost_cents     integer,                -- per-message cost, for margin tracking
  workflow_ref   text,                   -- n8n workflow id that produced it
  error          jsonb,

  sent_at        timestamptz NOT NULL DEFAULT now(),
  delivered_at   timestamptz,
  read_at        timestamptz,
  responded_at   timestamptz
);

CREATE INDEX message_tenant_time  ON core.message (tenant_id, sent_at DESC);
CREATE INDEX message_contact_time ON core.message (tenant_id, contact_id, sent_at DESC);
CREATE UNIQUE INDEX message_provider_id_uk ON core.message (provider, provider_msg_id)
  WHERE provider_msg_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Append-only event stream. Everything the automations do lands here, which
-- is how the portal renders a timeline without querying nine tables.
-- ---------------------------------------------------------------------------
CREATE TABLE core.event (
  id          uuid NOT NULL DEFAULT uuidv7(),
  tenant_id   uuid NOT NULL REFERENCES agency.tenant(id) ON DELETE CASCADE,
  contact_id  uuid REFERENCES core.contact(id) ON DELETE SET NULL,
  kind        text NOT NULL,             -- 'lead.captured','appointment.no_show'
  payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  -- A unique constraint on a partitioned table must contain every partition
  -- key column, so the PK is (id, occurred_at) rather than id alone. uuidv7()
  -- is still globally unique; the composite is a partitioning formality.
  PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);

-- Create partitions a quarter ahead; see tools/maintenance.py.
CREATE TABLE core.event_2026q3 PARTITION OF core.event
  FOR VALUES FROM ('2026-07-01') TO ('2026-10-01');
CREATE TABLE core.event_2026q4 PARTITION OF core.event
  FOR VALUES FROM ('2026-10-01') TO ('2027-01-01');

CREATE INDEX event_tenant_kind_time ON core.event (tenant_id, kind, occurred_at DESC);

-- ---------------------------------------------------------------------------
-- Attributed revenue. The single most important table in the entire system:
-- it is the reason a client renews. Every rand here must be traceable to a
-- specific automation touch, because "we think it helped" does not survive a
-- budget review.
-- ---------------------------------------------------------------------------
CREATE TABLE core.revenue_event (
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id      uuid NOT NULL REFERENCES agency.tenant(id) ON DELETE CASCADE,
  contact_id     uuid REFERENCES core.contact(id) ON DELETE SET NULL,
  amount_cents   bigint NOT NULL,
  currency       char(3) NOT NULL DEFAULT 'ZAR',
  kind           text NOT NULL,          -- 'commission','treatment','deposit_saved'
  -- What the system did that produced it, and how confident we are.
  attributed_to  text NOT NULL,          -- workflow slug
  attribution_confidence text NOT NULL DEFAULT 'assisted'
    CHECK (attribution_confidence IN ('direct','assisted','reported')),
  occurred_on    date NOT NULL DEFAULT current_date,
  notes          text,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX revenue_tenant_month ON core.revenue_event
  (tenant_id, occurred_on DESC, kind);

-- ===========================================================================
--  Row-level security
--
--  apply_tenant_rls() is called once per table, here and again at the bottom
--  of the niche migrations. Adding a table without calling it is the one
--  mistake in this codebase that silently leaks one client's data to another,
--  so 004_guardrails.sql fails the build if any tenant_id table is missing a
--  policy.
-- ===========================================================================
CREATE OR REPLACE FUNCTION core.apply_tenant_rls(p_table regclass) RETURNS void
  LANGUAGE plpgsql AS
$$
BEGIN
  EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', p_table);
  EXECUTE format('ALTER TABLE %s FORCE  ROW LEVEL SECURITY', p_table);
  EXECUTE format($f$
    CREATE POLICY tenant_isolation ON %s
      USING (tenant_id = core.current_tenant())
      WITH CHECK (tenant_id = core.current_tenant())
  $f$, p_table);
END
$$;

SELECT core.apply_tenant_rls(t) FROM unnest(ARRAY[
  'agency.integration'::regclass,
  'core.contact'::regclass,
  'core.consent'::regclass,
  'core.suppression'::regclass,
  'core.message'::regclass,
  'core.event'::regclass,
  'core.revenue_event'::regclass
]) AS t;

-- agency.tenant is the one table the app reads without a tenant context set
-- (it is how a webhook resolves a phone_number_id to a tenant in the first
-- place), so it gets a narrower policy instead.
ALTER TABLE agency.tenant ENABLE ROW LEVEL SECURITY;
ALTER TABLE agency.tenant FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_self ON agency.tenant
  USING (core.current_tenant() IS NULL OR id = core.current_tenant());

GRANT SELECT, INSERT, UPDATE, DELETE
  ON ALL TABLES IN SCHEMA core, agency, realestate, medspa TO agency_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA core, agency, realestate, medspa
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agency_app;

-- ---------------------------------------------------------------------------
-- updated_at maintenance
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.touch_updated_at() RETURNS trigger
  LANGUAGE plpgsql AS
$$ BEGIN NEW.updated_at := now(); RETURN NEW; END $$;

CREATE TRIGGER touch BEFORE UPDATE ON agency.tenant
  FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();
CREATE TRIGGER touch BEFORE UPDATE ON core.contact
  FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

-- ---------------------------------------------------------------------------
-- The gate every outbound marketing workflow must pass through.
--
-- Returns TRUE only when POPIA s69 is satisfied AND the identifier is not
-- suppressed. Utility/service messages (appointment reminders, transaction
-- updates) pass on the 'legitimate_service' basis, which is why the purpose
-- argument is mandatory rather than optional.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.may_contact(
  p_contact_id uuid,
  p_channel    core.channel,
  p_purpose    text
) RETURNS boolean
  LANGUAGE plpgsql STABLE AS
$$
DECLARE
  v_tenant     uuid := core.current_tenant();
  v_identifier text;
  v_consent    record;
BEGIN
  SELECT CASE WHEN p_channel = 'email' THEN lower(email::text) ELSE msisdn END
    INTO v_identifier
  FROM core.contact WHERE id = p_contact_id AND tenant_id = v_tenant;

  IF v_identifier IS NULL THEN
    RETURN false;
  END IF;

  IF EXISTS (SELECT 1 FROM core.suppression
             WHERE tenant_id = v_tenant
               AND identifier = v_identifier
               AND channel = p_channel) THEN
    RETURN false;
  END IF;

  SELECT * INTO v_consent
  FROM core.consent_current
  WHERE tenant_id = v_tenant
    AND contact_id = p_contact_id
    AND channel = p_channel
    AND purpose = p_purpose;

  IF NOT FOUND THEN
    RETURN false;                                    -- no record == no consent
  END IF;

  RETURN v_consent.granted
     AND (v_consent.expires_at IS NULL OR v_consent.expires_at > now());
END
$$;

COMMENT ON FUNCTION core.may_contact IS
  'POPIA s69 gate. Every marketing send in n8n calls this first and short-'
  'circuits on false. Fails closed: absence of a consent record is a refusal.';
