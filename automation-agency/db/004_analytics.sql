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

-- Rolling 30-day headline, computed from the base table rather than rolled up
-- from the weekly view.
--
-- Two reasons this exists. First, percentiles do not re-aggregate: the median
-- of six weekly medians is not the median. Second, calendar month-to-date is
-- the wrong window for a dashboard — on the 1st it is empty and on the 2nd it
-- drops the week straddling the month boundary, so a client logs in and sees
-- zero leads. Rolling 30 days is always populated and always honest.
CREATE VIEW analytics.v_headline_realestate WITH (security_invoker = true) AS
SELECT
  tenant_id,
  count(*)                                                     AS leads,
  count(*) FILTER (WHERE first_response_at IS NOT NULL)        AS responded,
  count(*) FILTER (WHERE response_seconds <= 300)              AS under_5_min,
  percentile_disc(0.5) WITHIN GROUP (ORDER BY response_seconds) AS p50_seconds,
  percentile_disc(0.9) WITHIN GROUP (ORDER BY response_seconds) AS p90_seconds
FROM realestate.lead
WHERE received_at >= now() - interval '30 days'
GROUP BY tenant_id;

-- Same window, for the number that renews the contract.
CREATE VIEW analytics.v_recovered_30d WITH (security_invoker = true) AS
SELECT
  tenant_id,
  round(sum(amount_cents) / 100.0, 2)                          AS rands,
  count(*)                                                     AS events
FROM core.revenue_event
WHERE occurred_on >= current_date - 30
GROUP BY tenant_id;

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

-- ---------------------------------------------------------------------------
-- Chart-shaped read models. Each one exists because a specific chart in the
-- portal needs it; keeping the shaping in SQL means the portal never does
-- arithmetic a client could dispute.
-- ---------------------------------------------------------------------------

-- Speed-to-lead as a distribution, not an average. Rendered as a bar chart so
-- the client sees the shape: a tall first bar is the product working, and the
-- long tail is a management conversation about a specific agent.
CREATE VIEW analytics.v_response_buckets WITH (security_invoker = true) AS
WITH bucketed AS (
  SELECT tenant_id,
         CASE
           WHEN first_response_at IS NULL      THEN 'No reply'
           WHEN response_seconds < 60          THEN 'Under 1 min'
           WHEN response_seconds < 300         THEN '1-5 min'
           WHEN response_seconds < 3600        THEN '5-60 min'
           WHEN response_seconds < 86400       THEN '1-24 hours'
           ELSE 'Over a day'
         END AS bucket
  FROM realestate.lead
  WHERE received_at >= now() - interval '30 days'
)
SELECT tenant_id, bucket, count(*) AS leads,
       CASE bucket
         WHEN 'Under 1 min' THEN 1 WHEN '1-5 min' THEN 2 WHEN '5-60 min' THEN 3
         WHEN '1-24 hours'  THEN 4 WHEN 'Over a day' THEN 5 ELSE 6
       END AS sort_order
FROM bucketed
GROUP BY tenant_id, bucket;

-- Cumulative recovered value by month against the retainer line. This is the
-- chart that renews the contract, so it is a first-class read model.
CREATE VIEW analytics.v_recovered_cumulative WITH (security_invoker = true) AS
SELECT r.tenant_id,
       date_trunc('month', r.occurred_on)::date AS month,
       round(sum(sum(r.amount_cents)) OVER (
         PARTITION BY r.tenant_id ORDER BY date_trunc('month', r.occurred_on)
       ) / 100.0, 2) AS cumulative_rands,
       round(sum(r.amount_cents) / 100.0, 2) AS month_rands
FROM core.revenue_event r
GROUP BY r.tenant_id, date_trunc('month', r.occurred_on);

-- Chair utilisation as a calendar: one row per trading day over the last
-- quarter. Rendered as a weekday x week heatmap, which is the shape the BKLit
-- heatmap is built for and the shape an owner reads instantly — the pale
-- columns are the weeks the diary did not fill.
CREATE VIEW analytics.v_utilisation_calendar WITH (security_invoker = true) AS
SELECT
  a.tenant_id,
  lower(a.during)::date                                   AS day,
  EXTRACT(ISODOW FROM lower(a.during))::int               AS weekday,   -- 1 = Monday
  count(*) FILTER (WHERE a.status = 'completed')          AS booked,
  count(*) FILTER (WHERE a.is_leakage)                    AS lost,
  round(sum(a.value_cents) FILTER (WHERE a.status = 'completed') / 100.0, 2)
                                                          AS earned_rands
FROM medspa.appointment a
WHERE lower(a.during) >= now() - interval '84 days'
GROUP BY 1,2,3;

-- Hour-of-day utilisation, kept separate. Percentiles and hour buckets do not
-- belong in the same view as the calendar: different grain, different chart.
CREATE VIEW analytics.v_utilisation_by_hour WITH (security_invoker = true) AS
SELECT
  a.tenant_id,
  EXTRACT(ISODOW FROM lower(a.during))::int               AS weekday,
  EXTRACT(HOUR  FROM lower(a.during))::int                AS hour,
  count(*) FILTER (WHERE a.status = 'completed')          AS booked,
  count(*) FILTER (WHERE a.is_leakage)                    AS lost
FROM medspa.appointment a
WHERE lower(a.during) >= now() - interval '84 days'
GROUP BY 1,2,3;

-- Recall funnel collapsed to stages, for a funnel chart. Each stage counts
-- every recall that reached it or beyond, so the funnel only ever narrows.
CREATE VIEW analytics.v_recall_stages WITH (security_invoker = true) AS
WITH r AS (
  SELECT rc.tenant_id, rc.status, t.price_cents
  FROM   medspa.recall rc
  JOIN   medspa.treatment_type t
         ON t.id = rc.treatment_type_id AND t.tenant_id = rc.tenant_id
)
SELECT tenant_id, 1 AS step, 'Due'::text AS stage, count(*) AS recalls,
       round(sum(price_cents) / 100.0, 2) AS rands
FROM r GROUP BY tenant_id
UNION ALL
SELECT tenant_id, 2, 'Contacted', count(*), round(sum(price_cents) / 100.0, 2)
FROM r WHERE status IN ('sent','engaged','booked','declined') GROUP BY tenant_id
UNION ALL
SELECT tenant_id, 3, 'Engaged', count(*), round(sum(price_cents) / 100.0, 2)
FROM r WHERE status IN ('engaged','booked') GROUP BY tenant_id
UNION ALL
SELECT tenant_id, 4, 'Rebooked', count(*), round(sum(price_cents) / 100.0, 2)
FROM r WHERE status = 'booked' GROUP BY tenant_id;

-- Med spa headline, rolling 30 days, mirroring the real-estate one.
CREATE VIEW analytics.v_headline_medspa WITH (security_invoker = true) AS
SELECT
  tenant_id,
  count(*)                                              AS appointments,
  count(*) FILTER (WHERE status = 'completed')          AS completed,
  count(*) FILTER (WHERE is_leakage)                    AS lost,
  round(sum(value_cents) FILTER (WHERE status = 'completed') / 100.0, 2)
                                                        AS earned_rands,
  round(sum(value_cents) FILTER (WHERE is_leakage) / 100.0, 2)
                                                        AS leaked_rands,
  round(100.0 * count(*) FILTER (WHERE is_leakage)
        / NULLIF(count(*), 0), 1)                       AS leakage_pct
FROM medspa.appointment
WHERE lower(during) >= now() - interval '30 days'
GROUP BY tenant_id;

-- ---------------------------------------------------------------------------
-- The portal needs the client's own trading name and retainer to render "a
-- 5.1x return against your retainer". It must NOT be able to read setup fees,
-- operator agreement dates, or the Information Officer's contact details —
-- and certainly not another client's commercial terms.
--
-- Hence a column-scoped grant plus a view with an explicit tenant predicate.
-- agency.tenant's own policy deliberately opens up when no tenant context is
-- set (the gateway needs that to resolve an inbound webhook), so this view
-- does not rely on RLS alone.
-- ---------------------------------------------------------------------------
CREATE VIEW analytics.v_tenant_context WITH (security_invoker = true) AS
SELECT id AS tenant_id, trading_name, niche, plan_tier,
       mrr_rands AS retainer_rands, timezone
FROM   agency.tenant
WHERE  id = core.current_tenant();

GRANT USAGE ON SCHEMA agency TO agency_portal_ro;
GRANT SELECT (id, trading_name, niche, plan_tier, monthly_retainer_cents,
              mrr_rands, timezone)
  ON agency.tenant TO agency_portal_ro;

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
