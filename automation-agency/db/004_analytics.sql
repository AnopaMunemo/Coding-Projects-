-- ===========================================================================
--  Read models for the client portal + build-time guardrails.
--
--  The portal NEVER queries base tables. It reads these views through the
--  agency_portal_ro role, and every view is security_invoker so row-level
--  security is evaluated as the portal's role rather than the view owner's.
--  Get this wrong and one client sees another's pipeline.
-- ===========================================================================

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------------
-- Shared: the "what did you actually do for me" panel.
-- ---------------------------------------------------------------------------
CREATE VIEW analytics.v_activity_daily WITH (security_invoker = true) AS
SELECT
  tenant_id,
  sent_at::date                                        AS day,
  channel,
  direction,
  count(*)                                             AS messages,
  count(*) FILTER (WHERE delivered_at IS NOT NULL)     AS delivered,
  count(*) FILTER (WHERE read_at IS NOT NULL)          AS read,
  count(*) FILTER (WHERE responded_at IS NOT NULL)     AS responded,
  round(sum(cost_cents) / 100.0, 2)                    AS cost_rands
FROM core.message
GROUP BY 1,2,3,4;

CREATE VIEW analytics.v_revenue_monthly WITH (security_invoker = true) AS
SELECT
  tenant_id,
  date_trunc('month', occurred_on)::date               AS month,
  kind,
  attribution_confidence,
  count(*)                                             AS events,
  round(sum(amount_cents) / 100.0, 2)                  AS rands
FROM core.revenue_event
GROUP BY 1,2,3,4;

-- ---------------------------------------------------------------------------
-- Real estate: speed to lead is the headline. The portal renders the p50/p90
-- as a distribution, not a mean — a single agent who replies in nine hours
-- drags an average into uselessness and clients notice.
-- ---------------------------------------------------------------------------
CREATE VIEW analytics.v_lead_response WITH (security_invoker = true) AS
SELECT
  tenant_id,
  date_trunc('week', received_at)::date                       AS week,
  source,
  count(*)                                                    AS leads,
  count(*) FILTER (WHERE first_response_at IS NOT NULL)        AS responded,
  count(*) FILTER (WHERE response_seconds <= 300)              AS under_5_min,
  percentile_disc(0.5) WITHIN GROUP (ORDER BY response_seconds) AS p50_seconds,
  percentile_disc(0.9) WITHIN GROUP (ORDER BY response_seconds) AS p90_seconds
FROM realestate.lead
GROUP BY 1,2,3;

CREATE VIEW analytics.v_pipeline WITH (security_invoker = true) AS
SELECT
  l.tenant_id,
  l.stage,
  l.assigned_agent_ref,
  count(*)                                             AS leads,
  round(sum(l.budget_max_cents) / 100.0, 2)            AS pipeline_rands,
  count(*) FILTER (WHERE l.is_high_intent)             AS high_intent,
  count(*) FILTER (WHERE l.next_touch_due_at < now())  AS overdue_touches
FROM realestate.lead l
WHERE l.stage NOT IN ('transferred','lost')
GROUP BY 1,2,3;

-- Mandates lapsing inside 45 days. Rendered as a countdown list in the portal;
-- principals check this one daily once they realise it exists.
CREATE VIEW analytics.v_mandate_expiry WITH (security_invoker = true) AS
SELECT
  tenant_id, id AS property_id, reference, suburb, agent_ref,
  asking_price_rands, mandate_expires,
  (mandate_expires - current_date) AS days_remaining
FROM realestate.property
WHERE status = 'active'
  AND mandate = 'sole'
  AND mandate_expires BETWEEN current_date AND current_date + 45;

-- ---------------------------------------------------------------------------
-- Med spa: utilisation and leakage, both in rands.
-- ---------------------------------------------------------------------------
CREATE VIEW analytics.v_chair_utilisation WITH (security_invoker = true) AS
SELECT
  a.tenant_id,
  date_trunc('week', lower(a.during))::date            AS week,
  a.room_id,
  r.name                                               AS room_name,
  sum(a.duration_minutes) FILTER (WHERE a.status = 'completed') AS booked_minutes,
  sum(a.duration_minutes) FILTER (WHERE a.is_leakage)  AS lost_minutes,
  round(sum(a.value_cents) FILTER (WHERE a.status = 'completed') / 100.0, 2) AS earned_rands,
  round(sum(a.value_cents) FILTER (WHERE a.is_leakage) / 100.0, 2)          AS leaked_rands
FROM medspa.appointment a
JOIN medspa.room r ON r.id = a.room_id AND r.tenant_id = a.tenant_id
GROUP BY 1,2,3,4;

-- The recall funnel — the number that renews the contract.
CREATE VIEW analytics.v_recall_funnel WITH (security_invoker = true) AS
SELECT
  rc.tenant_id,
  date_trunc('month', rc.due_on)::date                 AS month,
  tt.name                                              AS treatment,
  count(*)                                             AS due,
  count(*) FILTER (WHERE rc.status <> 'pending')       AS actioned,
  count(*) FILTER (WHERE rc.status = 'booked')         AS rebooked,
  round(100.0 * count(*) FILTER (WHERE rc.status = 'booked')
        / NULLIF(count(*), 0), 1)                      AS rebook_rate_pct,
  round(sum(tt.price_cents) FILTER (WHERE rc.status = 'booked') / 100.0, 2) AS recovered_rands
FROM medspa.recall rc
JOIN medspa.treatment_type tt
  ON tt.id = rc.treatment_type_id AND tt.tenant_id = rc.tenant_id
GROUP BY 1,2,3;

CREATE VIEW analytics.v_course_dropoff WITH (security_invoker = true) AS
SELECT
  a.tenant_id,
  a.course_id,
  a.contact_id,
  tt.name                                              AS treatment,
  tt.course_sessions                                   AS sessions_planned,
  max(a.session_number) FILTER (WHERE a.status = 'completed') AS sessions_done,
  max(upper(a.during)) FILTER (WHERE a.status = 'completed')  AS last_session_at,
  round((tt.course_sessions - coalesce(max(a.session_number)
     FILTER (WHERE a.status = 'completed'), 0)) * tt.price_cents
     / GREATEST(tt.course_sessions, 1) / 100.0, 2)     AS unrealised_rands
FROM medspa.appointment a
JOIN medspa.treatment_type tt
  ON tt.id = a.treatment_type_id AND tt.tenant_id = a.tenant_id
WHERE a.course_id IS NOT NULL
GROUP BY a.tenant_id, a.course_id, a.contact_id,
         tt.name, tt.course_sessions, tt.price_cents;

GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO agency_portal_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics
  GRANT SELECT ON TABLES TO agency_portal_ro;

-- The portal role needs SELECT on the underlying tables for security_invoker
-- views to resolve — RLS is what actually constrains it, not the grant.
GRANT SELECT ON ALL TABLES IN SCHEMA core, realestate, medspa TO agency_portal_ro;
GRANT USAGE  ON SCHEMA core, realestate, medspa TO agency_portal_ro;

-- ---------------------------------------------------------------------------
-- Guardrail. Any table carrying tenant_id without row-level security is a
-- cross-client data leak waiting to happen, so the build fails loudly here
-- rather than quietly in production six months from now. Add a table, forget
-- core.apply_tenant_rls(), and this migration refuses to apply.
--
-- Partitions are skipped: they inherit the partitioned parent's policies, and
-- the parent is checked.
-- ---------------------------------------------------------------------------
DO $$
DECLARE missing text[];
BEGIN
  SELECT array_agg(format('%s.%s', c.relnamespace::regnamespace, c.relname))
    INTO missing
  FROM pg_class c
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id'
  WHERE c.relkind IN ('r','p')
    AND NOT c.relispartition
    AND c.relnamespace::regnamespace::text IN ('core','agency','realestate','medspa')
    AND NOT c.relrowsecurity;

  IF missing IS NOT NULL THEN
    RAISE EXCEPTION 'Tables with tenant_id but no row-level security: %', missing;
  END IF;
END
$$;
