"use client";

/**
 * Thin client-side wrappers around the BKLit registry charts.
 *
 * The dashboards are React Server Components so the SQL never reaches the
 * browser; the charts need hooks and measurement, so they live behind this
 * boundary. Keeping the wrappers here also means the pages stay declarative
 * and every chart in the portal is configured the same way.
 *
 * Install the underlying components with:
 *   npx shadcn@latest add @bklit/area-chart @bklit/bar-chart \
 *                         @bklit/funnel-chart @bklit/heatmap-chart
 */

import {
  AreaChart,
  Area,
  Grid,
  XAxis,
  BarChart,
  Bar,
  BarXAxis,
  ChartTooltip,
  FunnelChart,
  HeatmapChart,
  HeatmapCells,
  HeatmapXAxis,
  HeatmapYAxis,
  HeatmapTooltip,
  HeatmapLegend,
  HeatmapInteractionProvider,
  HeatmapInteractionBoundary,
} from "@/components/charts";
import { curveNatural } from "@visx/curve";

/** Cumulative attributed value by month. The renewal chart. */
export function RecoveredAreaChart({
  data,
}: {
  data: { date: Date; recovered: number }[];
}) {
  return (
    <div className="h-[260px] w-full">
      <AreaChart data={data} animationDuration={1100}>
        <Grid horizontal />
        <Area
          dataKey="recovered"
          curve={curveNatural}
          strokeWidth={2.5}
          fillOpacity={0.35}
        />
        <XAxis />
        <ChartTooltip />
      </AreaChart>
    </div>
  );
}

/**
 * Speed-to-lead as a distribution.
 *
 * Never render this as an average. One agent replying in nine hours drags a
 * mean into uselessness, and the client will (rightly) stop trusting the
 * number. The shape tells the true story: a tall first bar is the system
 * working, and the tail is a specific person to talk to.
 */
export function ResponseBarChart({
  data,
}: {
  data: { bucket: string; leads: number }[];
}) {
  return (
    <div className="h-[240px] w-full">
      <BarChart data={data} xDataKey="bucket">
        <Grid horizontal />
        <Bar dataKey="leads" fill="var(--chart-line-primary)" lineCap="round" />
        <BarXAxis />
        <ChartTooltip />
      </BarChart>
    </div>
  );
}

/** Recall funnel: due → contacted → engaged → rebooked. */
export function RecallFunnel({
  data,
}: {
  data: { label: string; value: number; displayValue?: string }[];
}) {
  return (
    <div className="h-[260px] w-full">
      <FunnelChart
        data={data}
        orientation="horizontal"
        showPercentage
        showValues
        color="var(--chart-line-primary)"
      />
    </div>
  );
}

/**
 * Chair utilisation by weekday and hour.
 *
 * This is the chart that sells the med spa retainer in a single glance: the
 * pale block where Tuesday afternoon should be is money that cannot be
 * recovered, and no owner needs it explained.
 */
export function UtilisationHeatmap({
  data,
}: {
  data: { bin: number; bins: { bin: number; count: number; date: Date }[] }[];
}) {
  return (
    <HeatmapInteractionProvider>
      <HeatmapInteractionBoundary>
        <div className="flex w-full flex-col items-stretch gap-3">
          <HeatmapChart className="w-full" data={data} layout="fluid">
            <HeatmapCells />
            <HeatmapXAxis />
            <HeatmapYAxis />
            <HeatmapTooltip />
          </HeatmapChart>
          <HeatmapLegend />
        </div>
      </HeatmapInteractionBoundary>
    </HeatmapInteractionProvider>
  );
}
