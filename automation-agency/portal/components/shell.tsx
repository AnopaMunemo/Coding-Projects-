import type { ReactNode } from "react";

/**
 * Page furniture for every client dashboard.
 *
 * The client's own trading name goes top-left, not your agency's logo. This is
 * their reporting tool; you are the plumbing. Principals forward screenshots
 * of these pages into their own board packs, and your branding in the corner
 * of that screenshot is the thing that gets it cropped out.
 */
export function Shell({
  client,
  period,
  title,
  children,
}: {
  client: string;
  period: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="min-h-dvh">
      <header className="border-b border-black/5 dark:border-white/10">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
          <div className="flex items-center gap-3">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-[--chart-line-primary] text-sm font-bold text-white">
              {client.slice(0, 1)}
            </span>
            <span className="text-sm font-semibold tracking-tight">{client}</span>
          </div>
          <span className="text-xs uppercase tracking-widest text-neutral-500">
            {period}
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-6 py-10">
        <h1 className="mb-8 text-3xl font-semibold tracking-tight">{title}</h1>
        {children}
      </main>

      <footer className="mx-auto max-w-6xl px-6 pb-12 pt-4">
        <p className="text-xs leading-relaxed text-neutral-500">
          Figures are drawn live from your system. Attributed value counts only
          transactions the automations can be traced to — assisted attribution
          is flagged separately and excluded from the direct total.
        </p>
      </footer>
    </div>
  );
}

/**
 * Chart container. The caption is not decoration: a one-sentence plain-language
 * reading underneath is what gets a chart understood in the four seconds a
 * principal actually gives it.
 */
export function ChartCard({
  title,
  caption,
  right,
  children,
  className = "",
}: {
  title: string;
  caption: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={
        "flex flex-col rounded-2xl border border-black/5 bg-white p-6 " +
        "shadow-[0_1px_2px_rgba(0,0,0,0.04)] dark:border-white/10 " +
        "dark:bg-neutral-900 dark:shadow-none " + className
      }
    >
      <div className="mb-5 flex items-start justify-between gap-4">
        <h2 className="text-sm font-medium text-neutral-500 dark:text-neutral-400">
          {title}
        </h2>
        {right}
      </div>
      {children}
      <p className="mt-5 text-sm leading-relaxed text-neutral-600 dark:text-neutral-400">
        {caption}
      </p>
    </section>
  );
}
