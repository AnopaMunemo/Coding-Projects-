# Atlas Capital — Design System ("2026 Cinematic")

> **Why this file exists.** It is the single source of truth for the app's look. Instead of
> re-describing the aesthetic in every design prompt (hundreds of tokens each time), reference
> it: *"Restyle the Forex tab per DESIGN.md"* or *"new card, DESIGN.md tokens."* Token, rule,
> and rationale live together so Claude (or any designer) stays consistent across sessions.
> Pattern borrowed from `VoltAgent/awesome-claude-design` and `Software-Engineer-AI-Agent-Atlas`.
> The canonical values live in `app.py`'s `:root` CSS block — keep the two in sync.

## 1. Aesthetic in one line
Deep-black institutional terminal · ambient gradient orbs · glassmorphism cards · neon-cyan
accents · monospace numerics · cinematic depth. Think *Bloomberg terminal meets a 2026 Dribbble
concept*. Calm dark base, light used sparingly as signal.

## 2. Color tokens (verbatim from `app.py :root`)
| Token | Value | Use |
|---|---|---|
| `--bg` | `#05070E` | App background (near-black) |
| `--bg2` | `#080B16` | Secondary background |
| `--card` | `rgba(18,24,40,0.62)` | Glass card fill (with `backdrop-filter: blur`) |
| `--card-sol` | `#0E1422` | Solid card fallback |
| `--border` | `rgba(255,255,255,0.08)` | Hairline borders |
| `--border2` | `rgba(255,255,255,0.14)` | Emphasised border |
| `--accent` | `#00D4FF` | Primary accent (cyan) — links, focus, key numbers |
| `--accent2` | `#38BDF8` | Accent gradient partner |
| `--violet` / `--purple` | `#8B7CFF` | Secondary gradient (headings, sliders) |
| `--emerald` / `--success` | `#00E676` | LONG / profit / pass |
| `--danger` | `#FF5C6E` | SHORT / loss / fail |
| `--amber` | `#FF9F45` | **Gold / XAUUSD desk**, warnings |
| `--warn` | `#FFB020` | Caution |
| `--txt` / `--txt2` / `--txt3` | `#F4F7FF` / `#97A3BE` / `#56627E` | Primary / secondary / tertiary text |
| `--glow` | `rgba(0,212,255,0.22)` | Accent glow for shadows |

**Semantic rule:** green = long/up/pass, red = short/down/fail, **amber = gold**, cyan = neutral
emphasis. Never use green/red decoratively — they carry trading meaning.

## 3. Typography
- **Display / headings:** `Sora` 700–800 (hero gradient text `#FFF → --accent → --violet`).
- **Body / UI:** `Inter` 300–900.
- **Numbers / prices / metrics:** `JetBrains Mono` — all KPI values and tabular figures are
  monospace so digits align. `.k-value` = 2.05rem, weight 700, letter-spacing −0.5px.

## 4. Components
- **KPI card (`.kpi`)** — glass fill, `backdrop-filter: blur(18px)`, 1px gradient border via
  masked `:before`, monospace value, accent glow on key figures (`.k-accent` text-shadow).
- **Signal card (`.sig`)** — left bar coloured + glowing by direction
  (`.sig-long`→emerald, `.sig-short`→danger). Build each card as a **single
  non-indented concatenated HTML string** — 4-space indentation triggers Streamlit's
  Markdown code-block parser and leaks raw HTML (learned bug, see decisions log).
- **Section head (`.sec-head`)** — Sora, with a short accent dash `::before` and a fading
  hairline `::after`.
- **Ambient orbs** — large blurred radial gradients drifting via the `drift` keyframe, masked
  with a radial fade so edges never harden. Background texture grid is mask-faded too.
- **Buttons** — cyan gradient fill (`--accent → --accent2`), dark ink text `#04121A`.

## 5. Layout rules
- No Streamlit sidebar — everything lives in tabs; configuration sits in the **Settings** tab.
- Card radius 18–20px; section spacing ~26px; hairline (`--border`) separators, never heavy rules.
- Data-dense but breathable: monochrome surface, colour reserved for state and the gold desk.

## 6. Prompt shortcuts (save tokens)
- *"Add a KPI per DESIGN.md"* → glass `.kpi`, mono value, accent glow.
- *"Style this as the gold desk"* → amber (`--amber`) accent, not cyan.
- *"Make it cinematic"* → already defined here; don't re-describe orbs/glass/fonts.
- When in doubt, match an existing component in `app.py` rather than inventing new CSS.

## 7. Fintech design rules (from `ui-ux-pro-max-skill`)
Atlas is an **institutional trading** product — design must read as *trustworthy*, not "AI toy".
- **Trust palette carries the weight:** cyan (`--accent`), emerald (`--success`), amber (gold),
  on the near-black base. These signal precision/markets.
- **Anti-pattern — AI purple/pink as the dominant brand gradient.** The skill flags heavy
  purple/magenta (`--violet`, `--magenta`) as a *banking trust killer*. Atlas keeps them as a
  **sparing secondary** accent only (hero word, slider track) — never the primary surface or the
  numbers. If pushing further toward an institutional look, dial magenta back first.
- **Data legibility > decoration.** Green/red are reserved for trade state, never ornament.
- **Match motion to seriousness:** subtle ambient drift, no bouncy/elastic easings, no neon flicker.

## 8. Pre-delivery checklist (run before shipping any UI change)
Implemented globally in `app.py`'s accessibility block; verify per change:
- [ ] Text contrast ≥ **4.5:1** (body) — check `--txt2`/`--txt3` on dark cards.
- [ ] **Visible keyboard focus** ring on every interactive element (`:focus-visible`, cyan).
- [ ] **`cursor: pointer`** on all clickable controls.
- [ ] Hover transitions **150–300ms**, ease — no harsh snaps.
- [ ] **`prefers-reduced-motion`** honored (orbs/beams/shine pause). ✔ global rule in place.
- [ ] **No emoji as functional icons** — use SVG/glyphs; emoji are fine only as decorative accents.
- [ ] Responsive sanity at **375 / 768 / 1024 / 1440px** (Streamlit columns reflow).
- [ ] Color is never the *only* signal — pair with text/icon (color-blind safety).
