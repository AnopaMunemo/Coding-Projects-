import { KpiCard } from "@/components/kpi-card";
import { queryForTenant, rands, duration } from "@/lib/db";

/**
 * Real-estate dashboard.
 *
 * The layout is deliberate: "recovered vs retainer" comes first, alone, above
 * everything else. It is the number that renews the contract, so it is the
 * number the principal sees before they have scrolled.
 *
 * getTenantId() must resolve from the authenticated session — never from a
 * query parameter, a header, or a path segment.
 */
async function getTenantId(): Promise<string> {
  // Wire to your auth provider. Left explicit so it cannot be forgotten.
  throw new Error("getTenantId(): connect to the session before deploying");
}

type Headline = {
  recovered_rands: string;
  retainer_rands: string;
  leads: number;
  responded: number;
  under_5_min: number;
  p50_seconds: number | null;
  overdue_touches: number;
};

async function loadHeadline(tenantId: string): Promise<Headline> {
  const [row] = await queryForTenant<Headline>(
    tenantId,
    `
    SELECT
      COALESCE((SELECT rands FROM analytics.v_recovered_30d), 0) AS recovered_rands,
      (SELECT retainer_rands FROM analytics.v_tenant_context)     AS retainer_rands,
      COALESCE(h.leads, 0)                                       AS leads,
      COALESCE(h.responded, 0)                                   AS responded,
      COALESCE(h.under_5_min, 0)                                 AS under_5_min,
      h.p50_seconds,
      COALESCE((SELECT sum(overdue_touches) FROM analytics.v_pipeline), 0)
                                                                 AS overdue_touches
    FROM (SELECT 1) _
    LEFT JOIN analytics.v_headline_realestate h ON true
    `,
  );
  return row;
}

export default async function Dashboard() {
  const tenantId = await getTenantId();
  const h = await loadHeadline(tenantId);

  const multiple = Number(h.recovered_rands) / Math.max(Number(h.retainer_rands), 1);
  const responseRate = h.leads ? (h.responded / h.leads) * 100 : 0;
  const fastRate = h.leads ? (h.under_5_min / h.leads) * 100 : 0;

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      <header className="mb-10">
        <p className="text-sm font-medium uppercase tracking-widest text-neutral-500">
          Last 30 days
        </p>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight">Performance</h1>
      </header>

      {/* The renewal number, on its own, first. */}
      <div className="mb-6">
        <KpiCard
          label="Recovered value"
          value={rands(h.recovered_rands)}
          caption={
            `Against a retainer of ${rands(h.retainer_rands)} — ` +
            `a ${multiple.toFixed(1)}× return over the last 30 days.`
          }
        />
      </div>

      <div className="grid gap-6 sm:grid-cols-2">
        <KpiCard
          index={1}
          label="Median response time"
          value={duration(h.p50_seconds)}
          caption="Half of all enquiries were answered faster than this."
        />
        <KpiCard
          index={2}
          label="Answered within 5 minutes"
          value={`${fastRate.toFixed(0)}%`}
          caption={`${h.under_5_min} of ${h.leads} enquiries in the last 30 days.`}
        />
        <KpiCard
          index={3}
          label="Enquiries answered"
          value={`${responseRate.toFixed(0)}%`}
          caption={`${h.responded} of ${h.leads} received a reply.`}
        />
        <KpiCard
          index={4}
          label="Overdue follow-ups"
          value={String(h.overdue_touches)}
          caption={
            h.overdue_touches > 0
              ? "Leads past their next scheduled touch. Chase these first."
              : "Nothing overdue. Every lead is on cadence."
          }
        />
      </div>
    </main>
  );
}
