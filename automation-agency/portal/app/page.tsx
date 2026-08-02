import { KpiCard } from "@/components/kpi-card";
import { Shell, ChartCard } from "@/components/shell";
import { RecoveredAreaChart, ResponseBarChart } from "@/components/charts-client";
import { rands, duration } from "@/lib/db";
import { getTenantId } from "@/lib/session";
import {
  tenantContext, recovered30d, recoveredCumulative, realEstateHeadline,
  responseBuckets, pipelineByStage, mandateExpiry, agentScorecard,
} from "@/lib/queries";

export const dynamic = "force-dynamic";

const STAGE_LABEL: Record<string, string> = {
  new: "New", contacted: "Contacted", qualified: "Qualified",
  viewing_booked: "Viewing booked", offer_submitted: "Offer submitted",
  under_contract: "Under contract", nurture: "In nurture",
};

/**
 * Real-estate client dashboard.
 *
 * Ordering is deliberate and worth defending: attributed value comes first,
 * alone, above the fold. It is the only number that decides whether the
 * retainer survives its next review, so it does not compete with anything.
 * Everything below it exists either to explain that number or to give the
 * principal something to act on this morning.
 */
export default async function RealEstateDashboard() {
  const t = await getTenantId();

  const [ctx, recovered, cumulative, head, buckets, pipeline, mandates, agents] =
    await Promise.all([
      tenantContext(t), recovered30d(t), recoveredCumulative(t),
      realEstateHeadline(t), responseBuckets(t), pipelineByStage(t),
      mandateExpiry(t), agentScorecard(t),
    ]);

  const retainer = Number(ctx.retainer_rands);
  const multiple = Number(recovered.rands) / Math.max(retainer, 1);
  const fastPct = head?.leads ? (head.under_5_min / head.leads) * 100 : 0;
  const answeredPct = head?.leads ? (head.responded / head.leads) * 100 : 0;
  const totalCommission = pipeline.reduce((a, r) => a + Number(r.commission_rands || 0), 0);
  const totalOverdue = agents.reduce((a, r) => a + Number(r.overdue || 0), 0);

  const areaData = cumulative.map((r) => ({
    date: new Date(r.month),
    recovered: Number(r.cumulative_rands),
  }));

  return (
    <Shell client={ctx.trading_name} period="Last 30 days" title="Performance">
      {/* The renewal number, on its own. */}
      <div className="mb-6">
        <KpiCard
          label="Attributed value recovered"
          value={rands(recovered.rands)}
          caption={
            `From ${recovered.events} transactions traced to the system, against a ` +
            `retainer of ${rands(retainer)} — a ${multiple.toFixed(1)}× return.`
          }
        />
      </div>

      <div className="mb-6 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
        <KpiCard index={1} label="Median reply time"
          value={duration(head?.p50_seconds)}
          caption="Half of all enquiries were answered faster than this." />
        <KpiCard index={2} label="Answered under 5 min"
          value={`${fastPct.toFixed(0)}%`}
          caption={`${head?.under_5_min ?? 0} of ${head?.leads ?? 0} enquiries.`} />
        <KpiCard index={3} label="Enquiries answered"
          value={`${answeredPct.toFixed(0)}%`}
          caption={`${head?.responded ?? 0} of ${head?.leads ?? 0} received a reply.`} />
        <KpiCard index={4} label="Overdue follow-ups"
          value={String(totalOverdue)}
          caption={totalOverdue > 0
            ? "Leads past their scheduled touch. Chase these first."
            : "Every lead is on cadence."} />
      </div>

      <div className="mb-6 grid gap-6 lg:grid-cols-2">
        <ChartCard
          title="Attributed value, cumulative"
          caption={
            `Since go-live the system has been credited with ` +
            `${rands(cumulative.at(-1)?.cumulative_rands ?? 0)} in commission. ` +
            `Assisted attribution is included here and broken out separately in ` +
            `the monthly report.`
          }
        >
          <RecoveredAreaChart data={areaData} />
        </ChartCard>

        <ChartCard
          title="Speed to first reply"
          caption={
            `${fastPct.toFixed(0)}% of enquiries get an answer inside five minutes. ` +
            `The tail on the right is where deals leak — those are the ones worth ` +
            `looking at agent by agent.`
          }
        >
          <ResponseBarChart data={buckets} />
        </ChartCard>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Mandate expiry: the list principals check daily once they find it. */}
        <ChartCard
          title="Sole mandates expiring"
          caption="Each one is a renewal conversation that has to happen before the date, or the listing walks."
        >
          <ul className="divide-y divide-black/5 dark:divide-white/10">
            {mandates.map((m) => (
              <li key={m.reference} className="flex items-center justify-between py-3">
                <div>
                  <p className="text-sm font-medium">{m.suburb}</p>
                  <p className="text-xs text-neutral-500">
                    {m.reference} · {m.agent_ref} · {rands(m.asking_price_rands)}
                  </p>
                </div>
                <span
                  className={
                    "rounded-full px-2.5 py-1 text-xs font-medium tabular-nums " +
                    (m.days_remaining <= 7
                      ? "bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-300"
                      : m.days_remaining <= 21
                        ? "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
                        : "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300")
                  }
                >
                  {m.days_remaining} days
                </span>
              </li>
            ))}
            {mandates.length === 0 && (
              <li className="py-3 text-sm text-neutral-500">
                Nothing expiring in the next 45 days.
              </li>
            )}
          </ul>
        </ChartCard>

        <ChartCard
          title="Pipeline by stage"
          caption={`Indicative commission to the agency across open leads: ${rands(totalCommission)}, ` +
            `at 6% and a 50/50 split. Budget figures are what buyers told us, not pre-approvals.`}
        >
          <ul className="space-y-3">
            {pipeline.map((s) => {
              const max = Math.max(...pipeline.map((p) => p.leads));
              return (
                <li key={s.stage}>
                  <div className="mb-1 flex items-baseline justify-between text-sm">
                    <span>{STAGE_LABEL[s.stage] ?? s.stage}</span>
                    <span className="tabular-nums text-neutral-500">
                      {s.leads} · {rands(s.commission_rands)}
                    </span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-neutral-100 dark:bg-neutral-800">
                    <div
                      className="h-full rounded-full bg-[--chart-line-primary]"
                      style={{ width: `${(s.leads / max) * 100}%` }}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        </ChartCard>
      </div>
    </Shell>
  );
}
