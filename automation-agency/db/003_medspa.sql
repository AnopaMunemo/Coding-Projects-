-- ===========================================================================
--  Niche 2 — Med spas / aesthetic clinics: "sell them full calendars"
--
--  This is a yield-management problem wearing a healthcare costume. A clinic
--  with three treatment rooms and a R900 000 laser has a fixed hourly capacity
--  and zero inventory carry: an unsold 14:00 slot on Tuesday is gone forever.
--  Everything below exists to raise utilisation and cut leakage.
--
--  Four mechanics:
--    1. Clinically-correct recall. Botulinum toxin at 12-16 weeks, filler at
--       9-18 months, laser hair removal on a 6-8 session protocol, needling at
--       4-6 weeks. Missing the interval degrades the result — so the reminder
--       is a treatment-quality intervention, not marketing. That distinction
--       is both the ethical basis and the legal basis (see the note on POPIA
--       s26/s27 below).
--    2. No-show and same-day-cancel recovery: deposit links, tiered
--       confirmations, automatic waitlist backfill.
--    3. Consult-to-treatment conversion.
--    4. Package/course completion — patients who stop at session 3 of 8.
--
--  COMPLIANCE, NON-NEGOTIABLE
--  --------------------------
--  * Treatment history is SPECIAL PERSONAL INFORMATION under POPIA s26
--    (health). Processing is prohibited except under s27 — in practice, the
--    clinic's treatment relationship plus explicit patient consent. Encrypt
--    at rest, restrict the columns the portal can read, and keep clinical
--    detail out of message bodies entirely.
--  * Practitioners registered with the HPCSA are bound by the Council's
--    ethical rules on advertising and canvassing: no patient testimonials,
--    no comparative superiority claims, no guaranteed outcomes. NOTHING in
--    this system may auto-solicit a public review or republish patient
--    praise for an HPCSA-registered practitioner. Feedback capture is
--    private, and stays private, unless the clinic's own attorney signs off.
-- ===========================================================================

\set ON_ERROR_STOP on

CREATE TYPE medspa.appointment_status AS ENUM (
  'requested', 'booked', 'confirmed', 'arrived', 'completed',
  'no_show', 'cancelled_late', 'cancelled_early'
);

CREATE TABLE medspa.practitioner (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id     uuid NOT NULL REFERENCES agency.tenant(id) ON DELETE CASCADE,
  display_name  text NOT NULL,
  -- Drives which compliance ruleset applies to this practitioner's patients.
  registration_body text CHECK (registration_body IN ('HPCSA','SANC','none')),
  registration_no   text,
  calendar_ref  text,                       -- Google/365 calendar id
  is_active     boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE medspa.room (
  id           uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id    uuid NOT NULL REFERENCES agency.tenant(id) ON DELETE CASCADE,
  name         text NOT NULL,
  -- The device in the room, and what it costs to have idle. This is what
  -- turns a utilisation chart into a rand figure in the monthly report.
  device_name  text,
  device_monthly_finance_cents bigint,
  operating_hours jsonb NOT NULL DEFAULT '{}'::jsonb,
  is_active    boolean NOT NULL DEFAULT true,
  UNIQUE (tenant_id, name)
);

CREATE TABLE medspa.treatment_type (
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id      uuid NOT NULL REFERENCES agency.tenant(id) ON DELETE CASCADE,
  name           text NOT NULL,
  category       text,                      -- 'injectable','laser','skin','body'
  duration_minutes integer NOT NULL,
  price_cents    bigint NOT NULL,
  deposit_cents  bigint NOT NULL DEFAULT 0,

  -- Recall protocol. NULL means no clinical recall applies.
  recall_interval_days      integer,
  recall_window_days        integer DEFAULT 21,   -- tolerance either side
  course_sessions           integer,             -- e.g. 8 for laser hair removal
  course_interval_days      integer,

  requires_consult boolean NOT NULL DEFAULT false,
  is_active      boolean NOT NULL DEFAULT true,
  UNIQUE (tenant_id, name)
);

ALTER TABLE medspa.treatment_type
  ADD COLUMN revenue_per_hour_cents bigint
  GENERATED ALWAYS AS ((price_cents * 60) / GREATEST(duration_minutes, 1)) VIRTUAL;

COMMENT ON COLUMN medspa.treatment_type.revenue_per_hour_cents IS
  'Yield per chair-hour. Drives which gap the waitlist backfill tries to fill '
  'first — a 20-minute toxin top-up beats a 90-minute facial for the same slot.';

-- ---------------------------------------------------------------------------
-- Appointments.
--
-- Double-booking a room or a practitioner is the fastest way to lose a
-- clinic's trust, so it is prevented by the database rather than by careful
-- workflow design. Two GiST exclusion constraints, each scoped to exclude
-- cancelled rows — which is why these are EXCLUDE constraints and not PG18's
-- UNIQUE ... WITHOUT OVERLAPS: temporal unique constraints do not take a
-- partial WHERE clause, and a cancelled 14:00 must not block a rebooked 14:00.
-- (Where no partial predicate is needed — practitioner leave, room closures —
-- prefer the PG18 temporal syntax; see medspa.room_closure below.)
-- ---------------------------------------------------------------------------
CREATE TABLE medspa.appointment (
  id              uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id       uuid NOT NULL REFERENCES agency.tenant(id) ON DELETE CASCADE,
  contact_id      uuid NOT NULL REFERENCES core.contact(id) ON DELETE CASCADE,
  practitioner_id uuid NOT NULL REFERENCES medspa.practitioner(id),
  room_id         uuid NOT NULL REFERENCES medspa.room(id),
  treatment_type_id uuid NOT NULL REFERENCES medspa.treatment_type(id),

  during          tstzrange NOT NULL,
  status          medspa.appointment_status NOT NULL DEFAULT 'booked',

  -- Course tracking, e.g. session 3 of 8.
  course_id       uuid,
  session_number  integer,

  booked_via      text,                     -- 'whatsapp_bot','phone','walk_in','web'
  value_cents     bigint,
  deposit_paid_cents bigint NOT NULL DEFAULT 0,

  confirmed_at    timestamptz,
  cancelled_at    timestamptz,
  -- Free-text stays clinical-detail-free by policy; anything clinical belongs
  -- in the practice management system, not here.
  admin_note      text,

  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT appointment_room_no_overlap
    EXCLUDE USING gist (
      tenant_id WITH =, room_id WITH =, during WITH &&
    ) WHERE (status NOT IN ('cancelled_late','cancelled_early','no_show')),

  CONSTRAINT appointment_practitioner_no_overlap
    EXCLUDE USING gist (
      tenant_id WITH =, practitioner_id WITH =, during WITH &&
    ) WHERE (status NOT IN ('cancelled_late','cancelled_early','no_show'))
);

ALTER TABLE medspa.appointment
  ADD COLUMN duration_minutes integer
  GENERATED ALWAYS AS (
    (EXTRACT(EPOCH FROM (upper(during) - lower(during))) / 60)::int
  ) VIRTUAL;

-- STORED, not VIRTUAL: the expression references the appointment_status enum,
-- and PostgreSQL 18 does not yet support user-defined types in virtual
-- generated columns. Stored is arguably better here anyway — every leakage
-- query filters on it, and a stored column can be indexed.
ALTER TABLE medspa.appointment
  ADD COLUMN is_leakage boolean
  GENERATED ALWAYS AS (status IN ('no_show','cancelled_late')) STORED;

CREATE INDEX appointment_leakage ON medspa.appointment (tenant_id, lower(during))
  WHERE is_leakage;

CREATE INDEX appointment_upcoming ON medspa.appointment (tenant_id, lower(during))
  WHERE status IN ('booked','confirmed');
CREATE INDEX appointment_contact  ON medspa.appointment (tenant_id, contact_id, lower(during) DESC);
CREATE INDEX appointment_course   ON medspa.appointment (tenant_id, course_id, session_number)
  WHERE course_id IS NOT NULL;

-- Practitioner leave and room closures. No partial predicate needed here, so
-- this uses PG18's temporal primary key: the period column participates in the
-- key directly and overlapping leave becomes a constraint violation.
CREATE TABLE medspa.room_closure (
  tenant_id  uuid NOT NULL REFERENCES agency.tenant(id) ON DELETE CASCADE,
  room_id    uuid NOT NULL REFERENCES medspa.room(id) ON DELETE CASCADE,
  during     tstzrange NOT NULL,
  reason     text,
  PRIMARY KEY (tenant_id, room_id, during WITHOUT OVERLAPS)
);

-- ---------------------------------------------------------------------------
-- Recall queue. Generated from the last completed appointment plus the
-- treatment's clinical interval. A recall is due, sent, booked, or expired —
-- and the conversion rate on this table is the headline number in the monthly
-- report.
-- ---------------------------------------------------------------------------
CREATE TABLE medspa.recall (
  id              uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id       uuid NOT NULL REFERENCES agency.tenant(id) ON DELETE CASCADE,
  contact_id      uuid NOT NULL REFERENCES core.contact(id) ON DELETE CASCADE,
  treatment_type_id uuid NOT NULL REFERENCES medspa.treatment_type(id),
  source_appointment_id uuid REFERENCES medspa.appointment(id) ON DELETE SET NULL,

  due_on          date NOT NULL,
  window_opens_on date NOT NULL,
  window_closes_on date NOT NULL,

  status          text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','sent','engaged','booked','declined','expired')),
  attempts        integer NOT NULL DEFAULT 0,
  last_attempt_at timestamptz,
  booked_appointment_id uuid REFERENCES medspa.appointment(id) ON DELETE SET NULL,

  created_at      timestamptz NOT NULL DEFAULT now(),
  CHECK (window_closes_on >= window_opens_on)
);

-- One open recall per patient per treatment. Without this you will, sooner or
-- later, send a patient four "time for your top-up" messages in a week and
-- lose the account over it.
CREATE UNIQUE INDEX recall_one_open_per_treatment ON medspa.recall
  (tenant_id, contact_id, treatment_type_id)
  WHERE status IN ('pending','sent','engaged');

CREATE INDEX recall_due ON medspa.recall (tenant_id, window_opens_on)
  WHERE status = 'pending';

-- ---------------------------------------------------------------------------
-- Waitlist. The backfill engine: when a slot frees up, the highest-yield
-- eligible patient gets the offer first, with a short expiry so the slot is
-- not held hostage by someone who is not reading their phone.
-- ---------------------------------------------------------------------------
CREATE TABLE medspa.waitlist_entry (
  id              uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id       uuid NOT NULL REFERENCES agency.tenant(id) ON DELETE CASCADE,
  contact_id      uuid NOT NULL REFERENCES core.contact(id) ON DELETE CASCADE,
  treatment_type_id uuid NOT NULL REFERENCES medspa.treatment_type(id),
  practitioner_id uuid REFERENCES medspa.practitioner(id),
  earliest_on     date NOT NULL DEFAULT current_date,
  latest_on       date,
  preferred_windows jsonb NOT NULL DEFAULT '[]'::jsonb,  -- [{"dow":2,"from":"09:00"}]
  status          text NOT NULL DEFAULT 'waiting'
    CHECK (status IN ('waiting','offered','converted','expired','withdrawn')),
  offered_at      timestamptz,
  offer_expires_at timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX waitlist_active ON medspa.waitlist_entry
  (tenant_id, treatment_type_id, created_at)
  WHERE status = 'waiting';

-- ---------------------------------------------------------------------------
-- Deposits. Yoco / Payfast / Peach / Stitch payment links issued against a
-- booking. Introducing deposits is usually worth more to the clinic than
-- every reminder in the system combined — and it is the change owners resist
-- hardest, so pilot it on new patients only.
-- ---------------------------------------------------------------------------
CREATE TABLE medspa.deposit (
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id      uuid NOT NULL REFERENCES agency.tenant(id) ON DELETE CASCADE,
  appointment_id uuid NOT NULL REFERENCES medspa.appointment(id) ON DELETE CASCADE,
  provider       text NOT NULL,            -- 'yoco','payfast','peach','stitch','ozow'
  provider_ref   text,
  amount_cents   bigint NOT NULL,
  status         text NOT NULL DEFAULT 'requested'
    CHECK (status IN ('requested','paid','failed','refunded','forfeited','waived')),
  link_url       text,
  requested_at   timestamptz NOT NULL DEFAULT now(),
  settled_at     timestamptz,
  UNIQUE (provider, provider_ref)
);

CREATE TRIGGER touch BEFORE UPDATE ON medspa.appointment
  FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

SELECT core.apply_tenant_rls(t) FROM unnest(ARRAY[
  'medspa.practitioner'::regclass,
  'medspa.room'::regclass,
  'medspa.treatment_type'::regclass,
  'medspa.appointment'::regclass,
  'medspa.room_closure'::regclass,
  'medspa.recall'::regclass,
  'medspa.waitlist_entry'::regclass,
  'medspa.deposit'::regclass
]) AS t;
