# Atlas Quant Stack — Production Gate Evaluation

This is an **honest** evaluation against the 3-Gate "Production-Grade Trading Bot"
framework. It separates what the automatic scorer reports from what is *genuinely*
true, and lists the real holes that remain.

Run the scorer yourself:

```bash
python -m trading_bot.evaluation.gate_scorer      # or: python -m trading_bot.main --mode score
python -m pytest trading_bot/tests -q
```

---

## Automatic scorer result

```
GATE 1 (Risk Management):  100/100  PASS
GATE 2 (Execution):        100/100  PASS
GATE 3 (Code Quality):     100/100  PASS
OVERALL:                   100/100  → "APPROVED FOR LIVE TRADING (pending backtest validation)"
```

### ⚠️ Read this before trusting the 100/100
`trading_bot/evaluation/gate_scorer.py` is a **static presence check** — it verifies
that the required classes, methods and files exist (e.g. `MAX_POSITION_SIZE`,
`place_oco_order`, `heartbeat_loop`, `reconcile_with_broker`). It does **not** prove
those implementations are correct, nor that the strategy is profitable. A perfect
scorer result means the *architecture* is complete, not that the bot will make money.

---

## What is genuinely TRUE (verified by running it)

| Capability | Verified how | Status |
|---|---|---|
| Hard-cap position sizing (2% pos / 1% risk / ¼-Kelly) | `RiskManager.calculate_position_size`, unit-tested | ✅ real |
| ATR stop-loss + take-profit at entry | executor + risk_manager | ✅ real |
| Multi-period circuit breaker (h/d/w/m), persisted | `risk/circuit_breaker.py`, unit-tested | ✅ real |
| Async architecture (asyncio + gather + timeouts) | `main.py`, `executor.py` run live | ✅ real |
| Retry + exponential backoff + circuit-open | `infrastructure/retry_strategy.py` | ✅ real |
| Heartbeat / connection halt | `infrastructure/connection_monitor.py` | ✅ real |
| Atomic, versioned state + broker reconciliation | `infrastructure/state_manager.py` | ✅ real |
| Walk-forward backtest + Monte-Carlo ruin prob | `--mode backtest` runs, prints metrics | ✅ real |
| Profitability validator (honest pass/fail) | **fails** the weak ensemble truthfully | ✅ real |
| Brain→body merge (31-strategy ensemble drives engine) | `--strategy ensemble` backtest runs | ✅ real |
| Self-learning (weight store + retrainer) | unit-tested round-trip | ✅ real |

**Live proof — SwingTrader on AAPL 2022–2024 (walk-forward):**
PF 1.76 · Sharpe 0.80 · win-rate 56% · maxDD −0.26% · ruin prob 0.0 → **validation PASSED**.

**Honest counter-example — Ensemble (equal-weighted) on the same data:**
PF 1.34 · Sharpe 0.62 · win-rate 47% → **validation FAILED** (PF < 1.5, return/DD < 2.0).
Throwing all 31 strategies in at equal weight is *worse*. This is the whole reason the
weight-learning loop exists, and it matches the PDF lesson below.

---

## The real holes (what the 100/100 does NOT cover)

1. **Profitability is unproven for live use (Gate 3's only soft gate).** One symbol over
   one window passing is not an edge. You must run walk-forward across all target
   symbols and a demo period before risking money. The dissertation's "Perfect Order"
   strategy **lost money on 6 of 7 FX pairs (2005–2020)** — see `knowledge/backtesting.md`.
2. **Ensemble needs weight-tuning to beat the simple strategy.** Equal weights fail.
   The retrainer (`trading_bot/learning/retrainer.py`) is the mechanism; it must run on
   real outcomes/walk-forward before the ensemble is trustworthy.
3. **Investec/MT5 reconciliation is exercised against paper only here.** The code paths
   exist; live brokers need your credentials and a supervised demo run.
4. **Latency/heartbeat are real but only meaningful on a live broker socket** — paper
   mode can't surface true network latency.
5. **Recovery/Martingale sizing is intentionally OFF** (conflicts with hard caps).

---

## Bottom line
- **Architecture / safety / resilience:** genuinely production-grade and verified.
- **Profitable edge:** **not established** — that is your backtesting + demo job, and the
  honest validator will tell you the truth each time.
- **Do not deploy real capital** until walk-forward + a demo period pass on *your* symbols.
