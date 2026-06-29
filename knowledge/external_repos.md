# External Repos & Research — Distilled Lessons for Atlas

What each referenced repo/paper offers, an honest "use it or skip it" call, and the **concrete
action** taken (or proposed) for Atlas. Sandbox note: this environment blocks `git clone github.com`
(egress 403), so these were studied over the web, not vendored. Clone them on an open-internet
machine if you want the full source.

---

## 1. TradingAgents — paper (arXiv 2412.20138v7) + repo (Tauric Research)
**What:** Multi-agent LLM "trading firm": analysts (fundamental/sentiment/news/technical) →
bull-vs-bear researcher debate → trader → risk team → fund manager. LangGraph; ReAct prompting;
file-based reflection/memory.

**Reported results (paper Table 1, ~3-month window):**
| | CR% | ARR% | Sharpe | MaxDD% |
|---|---|---|---|---|
| AAPL | 26.62 | 30.5 | 8.21 | 0.91 |
| GOOGL | 24.36 | 27.58 | 6.39 | 1.69 |
| AMZN | 23.21 | 24.90 | 5.60 | 2.11 |

Beats rule-based baselines (MACD/KDJ-RSI/ZMR/SMA) and buy-&-hold by ~6% CR.

**Honest read:** the paper itself admits the window is only **3 months**, each prediction costs
**~11 LLM calls + ~20 tool calls**, and the "Sharpe 8" is inflated by a pullback-light period.
This is a research scaffold, not a proven live edge — same stance as Atlas's own EVALUATION.md.
Full reference + integration path in `knowledge/tradingagents_reference.md`.

**Action:** documented; proposed `LLMAgentStrategy(BaseStrategy)` as one *weighted* ensemble input
(hard-cap RiskManager keeps final authority). **Two design lessons adopted regardless of whether we
wire the agents in:**
- *Structured output > free-form NL between stages* — the paper blames the "telephone effect"
  (context decay over long NL chains) for weak multi-agent systems. Atlas already passes typed
  `Signal`/`TradeSignal` dataclasses, not prose — keep it that way.
- *A dedicated risk-debate stage improves drawdown.* Atlas's hard-cap `RiskManager` is the
  deterministic equivalent; keep risk authority separate from signal generation.

## 2. GOLD_ORB (yulz008) — XAUUSD opening-range breakout EA
**What:** MT5 EA. H1 gold. Opening range from the first candle of the session; range "final" after
≥3 candles consolidate inside; long above range high / short below low. TP 1200 pts, SL 400 pts,
trailing arms at +700 pts (trails 100 pts min), 1% risk/trade, **max 2 trades/day (1 long/1 short)**,
halts at 10% drawdown.

**Honest read:** ORB is a real, well-documented intraday edge but regime-dependent; the fixed
1200/400 point targets are gold-specific and must be re-tuned per broker spread/volatility.

**Action — IMPLEMENTED.** Added `GoldORB` to `strategies.py` (registered in `ALL_STRATEGIES`,
now 26 strategies). The pure signal does session-aware opening-range detection (falls back to a
rolling range on daily/non-datetime frames). The **execution-side parameters map to the engine**,
not the signal:
| GOLD_ORB EA param | Atlas home |
|---|---|
| TP 1200 / SL 400 pts | `RiskManager` ATR or fixed TP/SL at order build |
| Trailing +700/100 | executor trailing-stop logic |
| 1% risk/trade | `MAX_RISK_PER_TRADE` (already 1%) |
| Max 2 trades/day | engine trade-count guard (proposed) |
| 10% max DD halt | `CircuitBreaker` (already multi-period) |

## 3. graphify (safishamsi) — codebase → knowledge graph  *(token efficiency)*
**What:** `uv tool install graphifyy` (the PyPI name really is double-y) then `graphify install`.
A Claude Code / Cursor skill: `/graphify .` maps code+docs into `graph.html`, `GRAPH_REPORT.md`,
`graph.json` using tree-sitter (local, no API for code). MIT.

**Honest read:** legit and genuinely useful for a repo this size — lets an AI answer "what calls
`aggregate_signal`?" from the graph instead of re-reading files (saves prompt tokens). Adds two
generated artifacts; keep them out of git or commit `GRAPH_REPORT.md` only.

**Action:** recommended dev tool (run locally; the sandbox can't install it). Add `graph.html`,
`graph.json` to `.gitignore` if used. Directly serves the user's "use fewer tokens" goal.

## 4. awesome-claude-design (VoltAgent) — design system library  *(designer + tokens)*
**What:** 68 ready `DESIGN.md` files + a portable `SKILL.md`. Core idea: keep **token + rule +
rationale in one file** so you reference the design once instead of re-describing it every prompt.

**Action — ADOPTED.** Created root `DESIGN.md` codifying Atlas's 2026-cinematic tokens (colors,
fonts, components, semantic green/red/amber rules). Future design prompts become one line.

## 5. Software-Engineer-AI-Agent-Atlas (syahiidkamil)  *(AI memory / tokens)*
**What:** A Claude Code template (confusingly also named "Atlas"). Persistent agent identity in
`misc/self/`, à-la-carte skills, **decision_logs** (ADR-style) and **learning-from-mistakes**
folders, auto-loaded `DESIGN.md`. Token strategy: minimal upfront spec, log rejected branches so
future sessions don't replay the same fork.

**Action — PATTERN ADOPTED (not the scaffold).** Atlas already has `CLAUDE.md` +
`knowledge/decisions_log.md`; added a **`knowledge/lessons.md`** ("learning from mistakes") so
bugs we hit (HTML-leak, NaN confidence, DataFrame-ambiguous-bool) are recorded once and never
relearned — that is itself a token saver.

## 6–8. Reference-only (studied, not integrated)
- **nautechsystems/nautilus_trader** — production Rust/Python algo-trading platform (event-driven,
  nanosecond backtester). *Lesson:* its strict event-driven core + typed messages validate Atlas's
  async-executor design. Too heavy to adopt; mine it for execution patterns if we ever rebuild the
  engine core. *Not vendored.*
- **stefan-jansen/machine-learning-for-trading** — the canonical ML-for-trading book repo (alpha
  factors, feature engineering, backtrader, RL). *Lesson:* feature-engineering + walk-forward
  discipline for `framework.py`'s RF/SVR models; good source for new alpha factors. *Reference.*
- **TreborNamor/TradingView-Machine-Learning-GUI** — a TradingView ML strategy GUI. *Lesson:*
  UX ideas for surfacing ML predictions; overlaps the Desk's Quant Models tab. *Reference.*
- **rkt/rkt** — CoreOS's (archived) container runtime. **Unrelated to trading** — almost certainly
  a mis-paste in the list. *Skipped.*

---

## Net effect on Atlas this session
- **New tradeable edge:** `GoldORB` strategy (code, tested across 4 input shapes).
- **Fewer prompt tokens:** `DESIGN.md` (design system) + `knowledge/lessons.md` (don't-relearn) +
  recommend `graphify` for codebase Q&A.
- **Better design discipline:** semantic color/typography rules captured once.
- **Honest stance held:** no profitability promised; LLM-agent results are short-window and costly.
