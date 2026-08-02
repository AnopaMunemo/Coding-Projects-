-- ===========================================================================
--  Prospecting. Your own pipeline, not a client's.
--
--  Sits in the agency schema and is deliberately NOT tenant-scoped: these are
--  businesses you are pitching, not businesses you serve. Written to by
--  tools/prospect_audit.py.
--
--  POPIA note: a South African company is a "juristic person" and IS a data
--  subject under POPIA, so B2B cold outreach is not the free-for-all it is in
--  Europe. Section 69 permits a single approach to REQUEST consent. That is
--  why approach_count and approach_log exist, and why the audit tool refuses
--  to re-approach a prospect that has already been contacted or has opted out.
-- ===========================================================================

\set ON_ERROR_STOP on

CREATE TABLE agency.prospect (
  id              uuid PRIMARY KEY DEFAULT uuidv7(),
  niche           agency.niche NOT NULL,
  business_name   text NOT NULL,
  website         text,
  suburb          text,
  city            text,
  province        text,

  -- Contact discovery
  principal_name  text,
  principal_role  text,                    -- 'principal','owner','practice_manager'
  public_email    citext,
  public_phone    text,
  linkedin_url    text,

  -- Size / affordability proxies. A prospect that cannot carry R16 500/month
  -- is a waste of a first approach, however broken their systems are.
  headcount_estimate      integer,
  listing_count           integer,          -- real estate: live portal listings
  practitioner_count      integer,          -- med spa
  treatment_room_count    integer,
  google_review_count     integer,
  google_rating           numeric(2,1),
  last_review_at          date,

  -- Signals: the audit output. Each key is a detected gap, e.g.
  -- {"no_online_booking": true, "no_whatsapp": true, "no_crm_tag": true}
  signals         jsonb NOT NULL DEFAULT '{}'::jsonb,
  audit_raw       jsonb NOT NULL DEFAULT '{}'::jsonb,
  pain_score      integer NOT NULL DEFAULT 0,   -- 0-100, higher == more broken
  fit_score       integer NOT NULL DEFAULT 0,   -- 0-100, can they pay
  audited_at      timestamptz,

  status          text NOT NULL DEFAULT 'new'
    CHECK (status IN ('new','audited','approached','engaged','meeting_booked',
                      'proposal_sent','won','lost','disqualified','opted_out')),
  approach_count  integer NOT NULL DEFAULT 0,
  first_approached_at timestamptz,
  disqualify_reason text,

  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Expression in the uniqueness rule, so it has to be an index rather than a
-- table constraint. Branches of the same franchise in different suburbs are
-- separate prospects; the same branch scraped twice is not.
CREATE UNIQUE INDEX prospect_identity_uk
  ON agency.prospect (niche, lower(business_name), coalesce(lower(suburb), ''));

-- Composite priority: broken AND able to pay. Sorting on pain alone sends you
-- to the one-room salon with no website and no budget.
ALTER TABLE agency.prospect
  ADD COLUMN priority integer
  GENERATED ALWAYS AS ((pain_score * 6 + fit_score * 4) / 10) VIRTUAL;

CREATE INDEX prospect_queue ON agency.prospect (niche, status, pain_score DESC)
  WHERE status IN ('new','audited');

-- Every approach, logged. This is the record that answers a s69 complaint.
CREATE TABLE agency.approach_log (
  id           uuid PRIMARY KEY DEFAULT uuidv7(),
  prospect_id  uuid NOT NULL REFERENCES agency.prospect(id) ON DELETE CASCADE,
  channel      core.channel NOT NULL,
  purpose      text NOT NULL DEFAULT 'consent_request',
  script_ref   text,                        -- which script version was used
  body         text,
  outcome      text,                        -- 'no_answer','gatekeeper','spoke','booked'
  occurred_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX approach_log_prospect ON agency.approach_log (prospect_id, occurred_at DESC);

-- Hard block list. Anyone who says no, in any channel, ends up here forever.
CREATE TABLE agency.do_not_contact (
  identifier  text PRIMARY KEY,             -- normalised email or E.164 msisdn
  reason      text NOT NULL,
  added_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER touch BEFORE UPDATE ON agency.prospect
  FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();
