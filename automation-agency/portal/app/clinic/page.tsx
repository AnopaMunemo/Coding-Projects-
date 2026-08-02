import { KpiCard } from "@/components/kpi-card";
import { Shell, ChartCard } from "@/components/shell";
import { RecoveredAreaChart, RecallFunnel, UtilisationHeatmap } from "@/components/charts-client";
import { rands } from "@/lib/db";
import { getTenantId } from "@/lib/session";
import {
  tenantContext, recovered30d, recoveredCumulative,
  medSpaHeadline, utilisationCalendar, recallStages, topTreatments,
} from "@/lib/queries";

export const dynamic = "force-dynamic";

/**
 * Med-spa client dashboard.
 *
 * Same discipline as the real-estate view: attributed value first, then the
 * two charts that explain it. The heatmap is the one that sells the retainer
 * in a single glance — a pale block where Tuesday afternoon should be is money
 * that cannot be recovered, and no clinic owner needs that explained.
 *
 * Note what is NOT here: no patient names, no treatment histories, no clinical
 * detail. Health data is special personal information under POPIA s26 and the
 * portal has no business displaying it. Everything on this page is aggregate.
 */
export default async function MedSpaDashboard() {
  const t = await getTenantId();

  const [ctx, recovered, cumulative, head, grid, stages, treatments] =
    await Promise.all([
      tenantContext(t), recovered30d(t), recoveredCumulative(t),
      medSpaHeadline(t), utilisationCalendar(t), recallStages(t), topTreatments(t),
    ]);

  const retainer = Number(ctx.retainer_rands);
  const multiple = Number(recovered.rands) / Math.max(retainer, 1);

  // Calendar heatmap, GitHub-style: OUTER array is one entry per week
  // (a column), INNER bins are the seven weekdays (rows). Getting this the
  // wrong way round renders a transposed grid with nonsense axis labels —
  // which is exactly what it looks like, so check it visually after changing.
  const days = grid.map((g) => ({ ...g, d: new Date(g.day) }));
  const first = days[0]?.d ?? new Date();
  // Monday of the first week, so columns line up on week boundaries.
  const origin = new Date(first);
  origin.setDate(origin.getDate() - ((origin.getDay() + 6) % 7));
  const weekOf = (d: Date) =>
    Math.floor((d.getTime() - origin.getTime()) / (7 * 86_400_000));
  const weekCount = Math.max(...days.map((x) => weekOf(x.d)), 0) + 1;

  const heatData = Array.from({ length: weekCount }, (_, w) => ({
    bin: w,
    bins: Array.from({ length: 7 }, (_, dayIdx) => {
      const cellDate = new Date(origin.getTime() + (w * 7 + dayIdx) * 86_400_000);
      const hit = days.find((x) => x.d.toDateString() === cellDate.toDateString());
      return { bin: dayIdx, count: hit?.booked ?? 0, date: cellDate };
    }),
  }));

  const funnelData = stages.map((s) => ({
    label: s.stage,
    value: s.recalls,
    displayValue: String(s.recalls),
  }));

  const areaData = cumulative.map((r) => ({
    date: new Date(r.month),
    recovered: Number(r.cumulative_rands),
  }));

  const rebooked = stages.find((s) => s.stage === "Rebooked");
  const due = stages.find((s) => s.stage === "Due");
  const rebookRate = due?.recalls ? ((rebooked?.recalls ?? 0) / due.recalls) * 100 : 0;

  return (
    <Shell client={ctx.trading_name} period="Last 30 days" title="Performance">
      <div className="mb-6">
        <KpiCard
          label="Attributed value recovered"
          value={rands(recovered.rands)}
          caption={
            `From ${recovered.events} recovery events — rebookings, backfilled ` +
            `slots and resumed courses — against a retainer of ${rands(retainer)}. ` +
            `A ${multiple.toFixed(1)}× return.`
          }
        />
      </div>

      <div className="mb-6 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
        <KpiCard index={1} label="Treatments completed"
          value={String(head?.completed ?? 0)}
          caption={`${rands(head?.earned_rands ?? 0)} earned in the last 30 days.`} />
        <KpiCard index={2} label="Slots lost"
          value={String(head?.lost ?? 0)}
          caption={`${rands(head?.leaked_rands ?? 0)} in no-shows and same-day cancellations.`} />
        <KpiCard index={3} label="Leakage rate"
          value={`${head?.leakage_pct ?? 0}%`}
          caption="Industry norm for SA clinics is 15–30%. Deposits and waitlist backfill are what move this." />
        <KpiCard index={4} label="Recall rebooking rate"
          value={`${rebookRate.toFixed(0)}%`}
          caption={`${rebooked?.recalls ?? 0} of ${due?.recalls ?? 0} due recalls converted to a booking.`} />
      </div>

      <div className="mb-6 grid gap-6 lg:grid-cols-2">
        <ChartCard
          title="Attributed value, cumulative"
          caption={
            `${rands(cumulative.at(-1)?.cumulative_rands ?? 0)} recovered since ` +
            `go-live, from recalls, waitlist backfill and no-show recovery.`
          }
        >
          <RecoveredAreaChart data={areaData} />
        </ChartCard>

        <ChartCard
          title="Recall funnel"
          caption={
            `Of ${due?.recalls ?? 0} patients due for re-treatment, ` +
            `${rebooked?.recalls ?? 0} are back in the diary — ` +
            `${rands(rebooked?.rands ?? 0)} of treatment value.`
          }
        >
          <RecallFunnel data={funnelData} />
        </ChartCard>
      </div>

      <div className="grid items-start gap-6 lg:grid-cols-3">
        <ChartCard
          className="lg:col-span-2"
          title="Chair utilisation, last 12 weeks"
          caption="One cell per trading day, darker is busier. Wednesdays and Thursdays run near capacity; Mondays, Tuesdays and Saturdays are consistently thin — that pale band is capacity you are paying for and not selling, and it is where the waitlist and reactivation campaigns get pointed next."
        >
          <div className="h-[460px] w-full">
            <UtilisationHeatmap data={heatData} />
          </div>
        </ChartCard>

        <ChartCard
          title="Recalls by treatment"
          caption="Where the rebooking effort is actually paying."
        >
          <ul className="space-y-4">
            {treatments.map((tr) => (
              <li key={tr.treatment}>
                <div className="flex items-baseline justify-between gap-2 text-sm">
                  <span className="truncate">{tr.treatment}</span>
                  <span className="shrink-0 tabular-nums font-medium">
                    {rands(tr.recovered_rands ?? 0)}
                  </span>
                </div>
                <p className="mt-0.5 text-xs text-neutral-500">
                  {tr.rebooked}/{tr.due} rebooked · {tr.rebook_rate_pct ?? 0}%
                </p>
              </li>
            ))}
          </ul>
        </ChartCard>
      </div>
    </Shell>
  );
}
