# Client Portal

Next.js App Router + Tailwind + **Motion.dev**, with **Skiper-ui** and
**BKLit UI** components installed into `components/ui/`.

This is the authenticated surface. It runs on your own VPS, on the same Docker
network as PostgreSQL, reading through the `agency_portal_ro` role. Client
personal information never leaves your infrastructure — which is both the
correct POPIA posture and a genuine sales asset.

The public marketing site is a separate thing entirely and lives on
**Manus.im**. See §5.1 of the main [README](../README.md) for why the split
exists.

---

## Adding the component libraries

Skiper-ui and BKLit UI are shadcn-style registries: you run a CLI, the
component source lands in your repo, and you own it from that moment. That
ownership is the point — you are meant to edit them.

Neither Skiper-ui nor BKLit UI is an npm package — `npm i skiper-ui` 404s.
Both are **shadcn registries**, installed through the shadcn CLI:

```bash
# Motion is a normal dependency and is already in package.json
npm install

# One-time: register the namespaces in components.json
npx shadcn@latest init

# BKLit UI — the charts this portal uses
npx shadcn@latest add @bklit/area-chart @bklit/bar-chart \
                      @bklit/funnel-chart @bklit/heatmap-chart

# Skiper-ui — signature animated components
npx shadcn@latest add @skiper-ui/skiper40
```

`components.json` must declare both registries:

```json
"registries": {
  "@bklit": "https://bklit.com/r/{name}.json",
  "@skiper-ui": "https://skiper-ui.com/r/{name}.json"
}
```

Two things to know before you run this:

- **The CLI prompts interactively.** In CI or a container it will hang. The
  registry endpoints are plain JSON, so an unattended install can fetch the
  item, resolve `registryDependencies` recursively, and write the files itself.
- **BKLit ships no CSS variables.** Its components read `--chart-1`…`--chart-5`,
  `--chart-scale-01`…`05`, `--chart-line-primary` and friends. Define them or
  every chart renders black-on-black. Ours live in `app/globals.css`, light and
  dark — that file is the palette.

---

## Rules for this codebase

**Three animated moments per page. No more.** Skiper-ui is powerful enough that
using all of it makes your portal look like everyone else's. Pick the hero
moment, the proof moment, and one interaction. Stop.

**Never animate anything a user needs quickly.** Navigation, forms, phone
numbers, the logout button. A principal who cannot find the export button
because it fades in on scroll is a principal who stops logging in.

**One easing curve, everywhere.** `EASE` in `components/kpi-card.tsx` is
`[0.22, 1, 0.36, 1]`. Import it; do not hand-roll a second curve. Consistency
across surfaces is most of what reads as "expensive".

**`prefers-reduced-motion` on every animation.** `useReducedMotion()` from
Motion, plus the global CSS override in `app/globals.css`. This is an
accessibility requirement, not a preference.

**Every chart shows rands.** "47 recalls rebooked" means nothing to an owner.
"R164 500 recovered from 47 rebookings" means everything. Format with
`rands()` from `lib/db.ts` — `R164 500`, space separator, no gap after the R.

**Every chart carries a plain-language caption.** One sentence, underneath,
stating what changed. It beats any axis label and it is what gets screenshotted
into the client's own board pack.

**Skeletons, never spinners.** A spinner reads as broken.

**Light and dark, both, properly.** Half your users open this on a phone in a
car park at 19:00.

---

## Security invariants

Break any of these and one client sees another's pipeline.

1. `getTenantId()` resolves from the **authenticated session only**. Never from
   a query parameter, a header, or a path segment.
2. All data access goes through `queryForTenant()`, which sets `app.tenant_id`
   transaction-locally so RLS applies.
3. The portal queries `analytics.*` views only — never base tables. The views
   are `security_invoker`, which is what makes RLS evaluate as the portal role
   rather than the view owner.
4. The connection uses `agency_portal_ro`, which holds `SELECT` and nothing
   else. The portal is physically incapable of writing.
5. `staleTimes` is zero in `next.config.ts`. A cached render is a cross-tenant
   leak.
6. `robots: noindex` in the root layout.

---

## Performance budget

Test throttled, from a South African IP. Your fibre line is not your client's
phone on the N1.

| Metric | Budget |
|---|---|
| LCP (4G mobile) | < 2.5s |
| INP | < 200ms |
| CLS | < 0.1 |
| Initial route JS | < 180KB gzipped |
| Motion bundle | Lazy-loaded, below the fold only |
