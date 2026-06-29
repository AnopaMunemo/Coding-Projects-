# CLAUDE.md — Project Context for AI Sessions

This file orients any AI assistant (and human) working on this repo. Read it first.
When you make a notable decision or learn something, append it to
`knowledge/decisions_log.md`. That is how this project "keeps learning" across sessions —
the AI model itself does not retain memory between sessions; this file and `knowledge/` do.

## What this project is
**Atlas Capital** — an institutional-grade quant trading stack, JSE/ZAR-native, built for
South African retail (default budget R300). Two halves that work together:

- **The Desk** (repo root: `app.py` + `data_feed.py`, `portfolio_optimizer.py`,
  `forex_engine.py`, `strategies.py`, `framework.py`, `report.py`, `signal_export.py`)
  — a Streamlit "brain": 31-strategy library, HMM regime detection, ML forecasting
  (RandomForest/SVR/ARIMA in `framework.py`), Monte-Carlo, PDF reports, cinematic 2026 UI.
- **The Engine** (`trading_bot/`) — a production async "body": separated
  `strategy/ execution/ risk/ broker/ infrastructure/ backtest/ logging/ audit/ learning/`,
  multi-period circuit breakers, OCO stops, heartbeat, atomic state + reconciliation,
  walk-forward backtester, and an automatic 3-gate `evaluation/gate_scorer.py`.

Data flow: **Desk proposes signals → Engine applies hard-cap risk + executes async on the
chosen broker → state/audit written back → Desk monitors.**

## The merge (brain → body)
`trading_bot/strategy/ensemble.py` (`EnsembleStrategy`) wraps the Desk's
`strategies.run_all_strategies()` + `aggregate_signal()` behind the engine's
`BaseStrategy.calculate_signals()` contract, so the bot can run the whole library as one
weighted signal. Select it with `--strategy ensemble` (default `swing`).

## Design & token-efficiency
- **`DESIGN.md`** (repo root) is the single source of truth for the 2026-cinematic UI (color/
  font/component tokens). Reference it in design prompts instead of re-describing the look.
- **`knowledge/lessons.md`** records bugs already hit — check it before debugging.
- **`knowledge/external_repos.md`** distils the studied repos/papers (TradingAgents, GOLD_ORB,
  graphify, awesome-claude-design, nautilus_trader, …) and what was adopted from each.
- Tip: `graphify` (`uv tool install graphifyy` → `graphify install`) builds a codebase graph so
  AI sessions answer architecture questions without re-reading files (run locally; sandbox can't).

## Brokers (all three, switchable)
`--broker {paper, mt5, investec}` (or `BotConfig.broker_type`). paper = offline sim;
mt5 = MetaTrader demo via `Common\Files` bridge (`AtlasForexEA.mq5`); investec = live JSE,
needs `INVESTEC_*` env vars. See `RUN_GUIDE.md`.

## Learning system
- `trading_bot/learning/weight_store.py` — atomic, versioned per-strategy weights
  (`state/ensemble_weights.json`).
- `trading_bot/learning/retrainer.py` — rewards winning strategies / penalises losers from
  trade outcomes, and walk-forward-refits the ML ensemble.

## How to run / test (full detail in RUN_GUIDE.md)
```bash
python -m pytest trading_bot/tests -q                 # 11 tests
python -m trading_bot.main --mode score               # gate scorer
python -m trading_bot.main --mode backtest --strategy swing --symbols AAPL
python -m trading_bot.main --mode paper  --broker paper
streamlit run app.py
```

## Conventions
- Python 3.11+. Engine config is **frozen** (`trading_bot/config.py`) — change + redeploy,
  no live overrides. Hard risk caps are immutable by design.
- Atomic writes (temp → `os.replace`) for all persisted state.
- Strategies are pure (no I/O). Risk caps always win over signal conviction.
- Recovery/Martingale sizing exists but is **OFF by default** (unsafe with hard caps).

## Honest stance (do not overstate)
- The `gate_scorer` 100/100 is a **presence check**, not proof of profit — see `EVALUATION.md`.
- No profitability is promised. The equal-weighted ensemble currently **fails** validation;
  the focused swing strategy passes on AAPL but that is not a proven multi-symbol edge.
- The documented "Perfect Order" strategy lost money on 6/7 FX pairs — `knowledge/backtesting.md`.
- Never deploy real capital before walk-forward across your symbols + a demo period.

## Map of key files
- Engine entry: `trading_bot/main.py` · config: `trading_bot/config.py`
- Risk: `trading_bot/risk/{risk_manager,circuit_breaker,equity_monitor}.py`
- Execution: `trading_bot/execution/{executor,position_manager}.py`
- Infra: `trading_bot/infrastructure/{connection_monitor,retry_strategy,state_manager,latency_monitor,rate_limiter}.py`
- Backtest: `trading_bot/backtest/{engine,overfitting_detection,profitability_validator}.py`
- Merge + learning: `trading_bot/strategy/ensemble.py`, `trading_bot/learning/*`
- Knowledge base: `knowledge/*.md` (incl. `tradingagents_reference.md` — external LLM-agent framework + integration path) · Evaluation: `EVALUATION.md` · Run guide: `RUN_GUIDE.md`
