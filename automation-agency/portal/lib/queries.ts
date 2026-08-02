import { queryForTenant } from "./db";

/**
 * Every read the portal performs, in one file.
 *
 * All of these hit `analytics.*` views only — never base tables. The views are
 * security_invoker, so row-level security evaluates as the read-only portal
 * role. Adding a query that touches a base table would still work, and would
 * still be scoped by RLS, but it moves business logic out of SQL where the
 * client's numbers stop being reproducible from the database alone.
 */

export type TenantContext = {
  trading_name: string;
  niche: string;
  plan_tier: string;
  retainer_rands: string;
};

export async function tenantContext(t: string): Promise<TenantContext> {
  const [row] = await queryForTenant<TenantContext>(
    t,
    `SELECT trading_name, niche, plan_tier, retainer_rands
       FROM analytics.v_tenant_context`,
  );
  return row;
}

export async function recoveredCumulative(t: string) {
  return queryForTenant<{ month: Date; month_rands: string; cumulative_rands: string }>(
    t,
    `SELECT month, month_rands, cumulative_rands
       FROM analytics.v_recovered_cumulative
      ORDER BY month`,
  );
}

export async function recovered30d(t: string) {
  const [row] = await queryForTenant<{ rands: string; events: number }>(
    t,
    `SELECT COALESCE(rands, 0) AS rands, COALESCE(events, 0) AS events
       FROM analytics.v_recovered_30d`,
  );
  return row ?? { rands: "0", events: 0 };
}

// --- real estate -----------------------------------------------------------

export async function realEstateHeadline(t: string) {
  const [row] = await queryForTenant<{
    leads: number; responded: number; under_5_min: number;
    p50_seconds: number | null; p90_seconds: number | null;
  }>(t, `SELECT * FROM analytics.v_headline_realestate`);
  return row;
}

export async function responseBuckets(t: string) {
  return queryForTenant<{ bucket: string; leads: number }>(
    t,
    `SELECT bucket, leads FROM analytics.v_response_buckets ORDER BY sort_order`,
  );
}

export async function pipelineByStage(t: string) {
  // Combined buyer budget is a vanity number — "R276 million in play" makes a
  // principal distrust the whole page. What they actually think in is
  // commission to the agency, so the view's budget total is converted at a
  // 6% commission and a 50/50 agent split, and labelled as indicative.
  return queryForTenant<{ stage: string; leads: number; commission_rands: number }>(
    t,
    `SELECT stage, sum(leads)::int AS leads,
            round(sum(pipeline_rands) * 0.06 * 0.5, 2) AS commission_rands
       FROM analytics.v_pipeline
      GROUP BY stage
      ORDER BY sum(leads) DESC`,
  );
}

export async function mandateExpiry(t: string) {
  return queryForTenant<{
    reference: string; suburb: string; agent_ref: string;
    asking_price_rands: string; days_remaining: number;
  }>(t, `SELECT reference, suburb, agent_ref, asking_price_rands, days_remaining
           FROM analytics.v_mandate_expiry
          ORDER BY days_remaining
          LIMIT 6`);
}

export async function agentScorecard(t: string) {
  return queryForTenant<{
    assigned_agent_ref: string; leads: number; overdue: number; pipeline_rands: string;
  }>(t, `SELECT assigned_agent_ref, sum(leads)::int AS leads,
                sum(overdue_touches)::int AS overdue,
                round(sum(pipeline_rands), 2) AS pipeline_rands
           FROM analytics.v_pipeline
          WHERE assigned_agent_ref IS NOT NULL
          GROUP BY assigned_agent_ref
          ORDER BY sum(leads) DESC`);
}

// --- med spa ---------------------------------------------------------------

export async function medSpaHeadline(t: string) {
  const [row] = await queryForTenant<{
    appointments: number; completed: number; lost: number;
    earned_rands: string; leaked_rands: string; leakage_pct: string;
  }>(t, `SELECT * FROM analytics.v_headline_medspa`);
  return row;
}

export async function utilisationCalendar(t: string) {
  return queryForTenant<{ day: Date; weekday: number; booked: number; lost: number }>(
    t,
    `SELECT day, weekday, booked, lost
       FROM analytics.v_utilisation_calendar
      ORDER BY day`,
  );
}

export async function recallStages(t: string) {
  return queryForTenant<{ step: number; stage: string; recalls: number; rands: string }>(
    t,
    `SELECT step, stage, recalls, rands FROM analytics.v_recall_stages ORDER BY step`,
  );
}

export async function topTreatments(t: string) {
  return queryForTenant<{
    treatment: string; due: number; rebooked: number;
    rebook_rate_pct: string; recovered_rands: string;
  }>(t, `SELECT treatment, sum(due)::int AS due, sum(rebooked)::int AS rebooked,
                round(100.0 * sum(rebooked) / NULLIF(sum(due), 0), 1) AS rebook_rate_pct,
                round(sum(recovered_rands), 2) AS recovered_rands
           FROM analytics.v_recall_funnel
          GROUP BY treatment
          ORDER BY sum(recovered_rands) DESC NULLS LAST
          LIMIT 5`);
}
