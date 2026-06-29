# Project Decisions Log

A living record of architectural and policy decisions for this project. Append new decisions; do not rewrite history.

---

## Decisions

| Date | Decision | Rationale |
| ---- | -------- | --------- |
| 2026-06-19 | **Two systems merged:** `trading_bot/` (async execution engine, from BotV) + the Streamlit **"Desk"** (analysis brain at repo root). | **Desk = brain** (signals/analysis); **trading_bot = body** (async execution + risk). Clear separation of concerns. |
| 2026-06-19 | **All three brokers supported and switchable:** `paper` / `mt5` / `investec` via the `--broker` flag. | One codebase, swappable execution backends for paper testing, MT5, and Investec. |
| 2026-06-19 | **Learning model defined in two parts:** (a) the bot's ML models + ensemble weights **self-retrain** on walk-forward windows and trade outcomes; (b) this **knowledge base + CLAUDE.md** give every future AI session context. | The **AI model itself does NOT self-train between sessions** — durable knowledge lives in files, not in the model. |
| 2026-06-19 | **Recovery/Martingale sizing kept OFF by default.** | Recovery sizing conflicts with the hard-cap safety model (see `risk_management.md`). Safety first. |
| 2026-06-19 | **Profitability is never promised.** | Must be proven by the user's own backtests **plus a demo period** before any real money. See the honest lesson in `backtesting.md`. |
| 2026-06-29 | **Studied TradingAgents (Tauric Research) and documented it in `knowledge/tradingagents_reference.md`; did NOT vendor it.** | It's a multi-agent LLM framework (qualitative reasoning) — complementary to Atlas's quant engine, not overlapping. It needs paid LLM API keys and is non-deterministic, and the sandbox blocks cloning it. Captured the architecture + a concrete `LLMAgentStrategy(BaseStrategy)` integration path for when/if we wire it in, rather than adding the dependency now. |
| 2026-06-29 | **Added `GoldORB` opening-range-breakout strategy** (distilled from `yulz008/GOLD_ORB`) to `strategies.py`; registered in `ALL_STRATEGIES` (now 26). | Concrete new tradeable edge for the XAUUSD/gold focus. Pure signal (session-aware OR with rolling fallback); execution params (TP1200/SL400/trailing/2-trades-day/10%-DD) map to the engine's RiskManager/CircuitBreaker, keeping strategies I/O-free. |
| 2026-06-29 | **Created `DESIGN.md` (design-token system) + `knowledge/lessons.md` (don't-relearn log); recommend `graphify` for codebase Q&A.** | Serves the "use fewer prompt tokens / be a better designer" goal — reference the design once instead of re-describing it; record bugs once instead of rediscovering them. Patterns from `awesome-claude-design` and `Software-Engineer-AI-Agent-Atlas`. |
| 2026-06-29 | **Studied the TradingAgents paper (arXiv 2412.20138v7) and 7 referenced repos; vendored none.** | Sandbox blocks `git clone github.com` (403); studied over the web. `nautilus_trader`/`machine-learning-for-trading`/`TradingView-ML-GUI` kept as references; `rkt` skipped (unrelated container runtime, likely mis-pasted). Honest read recorded: TradingAgents' headline Sharpe is a 3-month, LLM-call-heavy result — not a proven live edge. |
| — | *Future decisions go here* | *(placeholder — this is a living document)* |
