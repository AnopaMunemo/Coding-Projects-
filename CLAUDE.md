# Project Instructions — XAUUSD Quant Trading Bot

> **Read this file first, every session.** Then read `VERSION_LOG.md`. Then the latest file in `versions/`. Only then write code.

---

## 1. What we're building

A self-improving bot that:

1. Predicts the direction of an asset (currently **XAUUSD** gold) using statistical, economic, mathematical, and ML signals.
2. Eventually trades autonomously: user deposits a bot-calculated minimum, runs the bot, bot trades to a profit target, halts.
3. Compounds — each cycle's gains size the next cycle's positions.

End goal output (paraphrased):

> "Deposit at least $X. Run the bot. It will trade XAUUSD with risk-gated position sizing, stop when account reaches $Y (your target), and never let drawdown exceed $Z."

---

## 2. Active configuration

| Item | Value | How to change |
|---|---|---|
| **Asset** | XAUUSD (`GC=F` primary, `GLD` ETF fallback) | `--ticker <symbol>` CLI flag, or `TICKER` env var, or edit `Config.xauusd_ticker` |
| **Capital** | $10,000 USD default | `--capital <usd>` CLI flag |
| **Branch** | `claude/stock-prediction-bot-lHwxP` | All bot dev pushes here |
| **Current version** | v8 (`versions/v8_goldbot.py`) | New work = `versions/v9_*.py` |
| **Archive** | 9 files in `versions/` (v2, v4, v5, v6×2, v7, v7.1, v7.2, v8). Note: uploaded v5.1 was byte-identical to v5 — collapsed. | |

**Swapping the ticker must always be a one-liner.** Never hard-code a symbol anywhere except `Config`. To switch from gold to SPY:

```bash
python bot.py --ticker SPY        # one command, no code edits
```

The macro basket (DXY, VIX, US10Y, ZAR) auto-adjusts if the new asset has different relevant macros — keep these in `Config` too.

---

## 3. Standards every new version MUST meet

A new version that fails any of these is not shippable. State explicitly in the version's docstring whether each gate passes.

1. **Buy-and-hold benchmark on the same window.** If net Sharpe ≤ B&H Sharpe, the bot output must say so plainly: `✗ does NOT beat buy-and-hold — just hold the asset`. No spin.
2. **Net-of-friction.** Report gross AND net Sharpe/return. Friction for XAUUSD ≈ 0.03% per side (spread 0.02% + slippage 0.01%). For JSE: 0.85% per round-trip.
3. **Walk-forward OOS validation.** Train ≥ 504 days, OOS slice = 21 days, non-overlapping. No in-sample-only Sharpe claims.
4. **Real data, not synthetic.** Final verdict always on real yfinance history. Synthetic data is fine for unit-testing the engine, not for declaring a strategy works.
5. **Honest report.** Headline metric + trade count + turnover + max drawdown. Not just Sharpe.
6. **Reproducible.** `random_state=42` everywhere; expose a seed override.
7. **VERSION_LOG entry.** Each new version appends to `VERSION_LOG.md`: what improved, what broke, what was removed, measured improvement on real data, known issues carried.

---

## 4. Risk discipline (never violate)

| Rule | Value |
|---|---|
| Kelly fraction | Fractional, ≤ 0.25 (quarter-Kelly) |
| Hard position cap | ≤ 20% NAV |
| Sizing formula | `min(kelly, 2 × CVaR_1d, hard_cap)` |
| Crisis regime (HMM=2) | size × 0.5–0.6 |
| Low regime confidence | linear scale-down to 0 |
| GARCH σ sanity | reject if outside (0.05%, 10%) daily — EWMA(λ=0.94) fallback |
| CVaR | EVT-GPD with historical fallback |
| GARCH refit cadence | every 21 trading days (not daily) |
| HMM refit cadence | every 63 trading days |
| Stop loss | ATR-based, 2× ATR(14) default |
| Take profit | 2:1 R:R default |

When the bot graduates to live trading (v11+):

- Max consecutive losses circuit breaker → halt + alert
- Daily loss limit → halt for the day
- Black swan jump detection → halt + manual approval to resume

---

## 5. Anti-patterns — paid for in blood, do NOT repeat

| ❌ Mistake | Where it bit us | Fix |
|---|---|---|
| All-or-nothing weight attribution | v2 | Per-feature direction reward (correct → +lr, wrong → −lr) |
| Pre-friction Sharpe headlines | v6 | Always report net AND gross |
| 13+ hand-weighted features | v6 | Cap ≤ 5 with theoretical grounding + ML layer |
| Synthetic-data verdicts | v7 (looked great) → v7.2 (real data was sobering) | Always conclude on real data |
| No walk-forward | v4 | 504-day train, 21-day OOS, non-overlapping |
| Multi-ticker yfinance KeyError | v5 | Handle MultiIndex columns explicitly + GARCH `rescale=True` |
| Daily GARCH/HMM refit | v6 (slow + unstable) | 21d / 63d cadences |
| Ignoring HMM posterior confidence | v6 (whipsaws near boundaries) | Scale size by `regime_conf` |
| Live trade with no paper audit | v6 added EquityCurveEngine — keep it | Mandatory paper-mode equity curve |
| Hard-coded tickers | every version through v7 | Lives only in `Config` |
| ML retraining on data including today | v8 | Hold out current day from training |

---

## 5b. Empirical baseline (from `backtests/strategy_bakeoff.py`, 2026-05-28)

**Buy-and-Hold XAUUSD is the strongest strategy on 11 years of real data** (Sharpe 0.47, CAGR 12.3%, MaxDD −20.9%, net of 3 bps/side friction). Zero of seven timing strategies beat it on full-window net Sharpe.

Therefore v9+ design **must not** assume that signal-timing beats holding. The target value proposition is *drawdown reduction*, not return enhancement. Read `backtests/winner.json` + the v9 entry in `VERSION_LOG.md` before designing.

## 6. Roadmap to autonomous trading

| Phase | What ships | Gate to next phase |
|---|---|---|
| **v9** | Strategy bake-off winner becomes core. Minimum-deposit calculator: given user risk + worst-case drawdown, output minimum USD. | Real-data Sharpe ≥ 0.6, beats B&H net |
| **v10** | Profit-target halt + compounding. Config: `profit_target_pct`, `stop_on_target=True`. Re-sizes from live NAV each run. | 3 months green paper-trade |
| **v11** | Broker API (OANDA v20 REST for XAUUSD). **Paper-mode only.** Real-money flag gated behind typed confirmation phrase. | 6 months paper, walk-forward OOS Sharpe ≥ 0.8 |
| **v12** | Real-money live trading. Circuit breakers active. | — |

---

## 7. Suggested enhancements (ranked by expected value)

Use these as v9+ ideas. Don't add everything — add only what survives the bake-off.

**High value:**
- Multi-timeframe entry (daily for direction, hourly for entry timing)
- Donchian / volatility breakout signal (gold trends well — orthogonal to EMA crossover)
- Gold-specific: real yields (US10Y TIPS), DXY z-score, COT positioning data
- Seasonality factor (gold has documented Sep–Feb strength)
- Stress tests on 2008, March-2020, 2022 (must survive all three)
- Equity curve PNG written each run
- Optuna hyperparameter optimization for EMA periods, RSI levels, stop multipliers
- Feature decay monitor — if a feature's contribution drops below threshold for 60 days, prune it

**Medium value:**
- Pairs trade overlay: XAUUSD vs Silver (SI=F), gold miners ETF (GDX), or platinum
- Sentiment v2: Fed minutes NLP + central bank gold purchases data
- Risk parity allocation if multi-asset
- Tail hedge: long small OTM put when HMM crisis regime triggers
- Streamlit dashboard (live equity, signal heatmap, regime timeline)
- Telegram interactive buttons (approve / reject / size-down)

**Speculative / be careful:**
- LSTM / Transformer on price (almost always overfits — only with strong walk-forward)
- Reinforcement learning agent (huge sample requirement — synthetic markets first)
- Order-book microstructure features (needs tick data, not daily)
- Twitter/Reddit sentiment (noisy; only useful if you have a quality NLP layer)

**Architectural:**
- Replace dataclass Config with `config.yaml` + profile presets (`conservative`, `moderate`, `aggressive`)
- Unit tests for indicators (`pytest tests/`)
- CI: GitHub Actions runs the bake-off on every push; fails if winner Sharpe drops > 10%
- Structured logging (JSON to `state/logs/`)
- Async data fetching (`asyncio` + `aiohttp`) for multi-asset

---

## 8. Output contract (every run produces)

1. **Signal**: action (STRONG_BUY / BUY / HOLD / SELL / STRONG_SELL) + probability + confidence label
2. **Trade levels**: entry, SL, TP, units, notional USD, risk USD, R:R
3. **Risk dashboard**: σ_daily (GARCH), CVaR_1d (EVT), Kelly %, regime label + confidence, Hurst, cointegration z
4. **Monte Carlo fan**: 21d and 63d p5/median/p95
5. **Walk-forward stats**: OOS Sharpe, win rate, max DD, # periods
6. **Signal breakdown**: bullet reasons (EMA, RSI, DXY, VIX, US10Y, coint, regime, MC)
7. **Buy-and-hold verdict**: ✓ beats / ~ matches / ✗ does not beat
8. **Self-learning stats**: all-time accuracy, last-10 accuracy
9. **Disclaimer**: Educational. Not financial advice.

---

## 9. State files (everything persistent lives here)

| File | Purpose |
|---|---|
| `state/{ticker}_weights.json` | Adaptive feature weights |
| `state/{ticker}_history.json` | Predictions + realized outcomes (≤ last 500) |
| `state/{ticker}_equity.json` | Paper-trade equity curve (v10+) |
| `state/{ticker}_orders.json` | Order audit trail (v11+) |
| `state/{ticker}_model.pkl` | Trained ML model snapshot |
| `backtests/winner.json` | Latest bake-off winner (informs v9+ base) |

---

## 10. Operational rules

- All bot work commits to `claude/stock-prediction-bot-lHwxP`. Never push elsewhere without explicit user permission.
- Never open a PR unless the user asks.
- Never run real-money trades from a Claude session, ever. Paper-mode only from inside the assistant.
- Before any "strategy works!" claim, run `python backtests/strategy_bakeoff.py` on real data and check it actually does.

---

## 11. Glossary (so signals aren't black boxes)

- **EMA** = Exponential Moving Average. Reacts faster than SMA. EMA(20) > EMA(50) is a short-term bullish trend.
- **RSI** (Wilder) = momentum oscillator 0–100. < 30 oversold, > 70 overbought.
- **GARCH(1,1)** = volatility model that captures volatility clustering. Better than rolling std.
- **EVT-CVaR** = Expected loss in the worst tail, using Extreme Value Theory on the Generalized Pareto distribution.
- **Hurst exponent** = H > 0.55 trending, H < 0.45 mean-reverting, ~0.5 random walk.
- **Cointegration z-score** = how far the residual from regressing gold on ZAR is from its mean. |z| > 1.5 means stretched.
- **Kelly criterion** = optimal bet sizing for max log-growth. Always use a fraction (we use 0.25×).
- **HMM regime** = hidden Markov model classifying current state as Bull / Calm / Crisis.
- **Walk-forward OOS** = train on past, test on un-seen future, slide forward. The honest backtest.
- **Buy-and-hold (B&H)** = the benchmark. If you can't beat it, don't trade — just hold.
