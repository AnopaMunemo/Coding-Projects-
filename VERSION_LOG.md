# Version Log

Append-only. Newest first. Read this before starting any new version.

Each entry follows:

```
## vN — YYYY-MM-DD — one-line summary
Asset: <ticker>
Improved:  <new capability vs prior>
Fixed:     <bugs squashed>
Removed:   <features deliberately cut>
Measured:  <real-data Sharpe, B&H delta, OOS win rate>
Known issues carried: <bugs still present>
```

---

## v9 — TBD — Design pivoted by bake-off result
Status: design phase.

### Bake-off result (2026-05-28, real XAUUSD `GC=F` 2015-01-01 → today, 2866 bars, 3 bps/side friction)

| Rank | Strategy | Sharpe | CAGR | MaxDD | Trades |
|---|---|---|---|---|---|
| 🥇 | **buy_and_hold** | **0.47** | **12.3%** | **−20.9%** | 2 |
| 2 | trend_ema_50_200 | 0.25 | 8.2% | −30.8% | 16 |
| 3 | vol_targeted_trend | 0.23 | 7.4% | −28.6% | 50 |
| 4 | trend_ema_20_50_atr_stop | 0.01 | 4.6% | −28.0% | 398 |
| 5 | rsi_mean_revert | −0.15 | 3.3% | −13.7% | 20 |
| 6 | donchian_breakout_20 | −0.29 | 1.1% | −24.8% | 144 |
| 7 | ensemble_vote | −0.43 | −0.3% | −31.6% | 144 |
| 8 | ml_logistic | −1.19 | 0.0% | −11.8% | 40 |

**0 of 7 active strategies beat buy-and-hold on net Sharpe over the full 11-year window.**

Sub-windows tell a more nuanced story:
- 2015-2019 (bull-weighted): B&H still wins. Sharpe 0.08, but every active strategy was negative.
- Last 3y (bull market): `vol_targeted_trend` Sharpe 1.38 narrowly beats B&H Sharpe 1.35 — by *reducing drawdown* (−11.7% vs −17.7%), not by adding return.

### Implications for v9 design

The bake-off destroys the assumption that timing gold improves on holding it. v9 must be **honest about this** and target a different value proposition than "more return":

1. **Reframe the goal**: not "beat B&H CAGR", but "match B&H return at half the drawdown". `vol_targeted_trend` already half-demonstrates this — refine it.
2. **Position-modulator architecture**: signals scale position size (0.0 → 1.0), not on/off. Most days hold near 1.0, scale down only when the *combined* risk signal flashes — high VOV + crisis-regime HMM + DXY breakout.
3. **Crisis-hedge overlay**: small long-vol or long-puts position when HMM regime 2 fires. Even if it costs 1-2%/yr in carry, it could halve the max DD.
4. **Drop the ML logistic in v8 form**: it produced a flat equity curve (Sharpe −1.19). Cause: trained on next-day direction, which gold's auto-correlation makes near-random. Either (a) longer prediction horizon (21-day, 63-day), or (b) drop ML and lean on regime/vol features only.
5. **Minimum-deposit calculator** (still on plan): given user risk tolerance + worst-1%-percentile drawdown from this real-data backtest, output USD floor that survives the historic −20.9% B&H drawdown with a configurable safety margin.
6. **ML training hold-out**: never train on today's row (carries over from v8 leak fix).

### Action items for v9
- Implement bot core as a B&H + drawdown-control overlay, not a directional timing system.
- Re-run bake-off with new strategies before declaring v9 ready: `bh_with_crisis_cash`, `bh_with_vol_brake`, `regime_scaled_bh`.
- Walk-forward must include a drawdown-stress sub-window (e.g. 2020-03 COVID crash, 2022-Q1 inflation shock).

---

## v8 — 2026 — `versions/v8_goldbot.py` — Adaptive ML self-retrain, XAUUSD pivot
Asset: XAUUSD (`GC=F`)
Improved:
- Adaptive ML model: logistic regression + GBT ensemble, retrains every run on full history
- Self-improving feature weights with per-feature reward attribution
- Trade levels output: entry / SL (2× ATR) / TP (2:1 R:R) / units / notional
- Prediction history log with daily accuracy backfill
- Macro basket expanded: DXY, VIX, US10Y, ZAR

Fixed:
- v5 multi-ticker yfinance KeyError (handles MultiIndex columns)
- v6 GARCH rescale + sanity check

Known issues carried:
- ML retrains on full history *including the current bar* → mild look-ahead. Fix in v9 by holding out the last row from training.
- Hurst & cointegration features are computed globally then stamped onto every training row (`hurst=0.5`, `coint_z=0.0` per-row). Wastes the features for ML. Compute per-row in v9.
- Capital sizing assumes XAUUSD as the only position. No multi-asset support yet.

---

## v7.2 — 2025 — `versions/v7_2_real_data_backtest.py` — Real historical data verdict
Asset: Gold spot USD (datasets/gold-prices monthly mirror)
Improved:
- First version benchmarked on **real** historical gold prices instead of synthetic data
- 5 strategies compared: buy-and-hold, trend (5/20), trend+stop (5/20, 15% TS), trend (3/12), trend+stop (3/12, 20% TS)

Measured (real monthly gold, 2000 → 2026):
- Buy-and-hold dominated most trend strategies after JSE friction
- Sobering verdict: trend-following only modestly beats B&H on Sharpe, with much higher turnover

Known issues:
- Monthly resolution only (data source limitation)
- No ML, no regime detection — pure mechanical signals

---

## v7.1 — 2025 — `versions/v7_1_strategy_comparison.py` — Strategy head-to-head framework
Asset: GLD.JO synthetic (calibrated to documented gold-ZAR stats)
Improved:
- Vol-targeted backtester (target 10% portfolio vol)
- Four strategies side-by-side: BuyAndHold, TrendFollowing, TrendPlusCointegration, MLProbabilistic
- 5-seed robustness check (42, 1337, 2024, 9999, 31415)
- Aggregate-across-seeds table — kills cherry-picking

Measured:
- Trend+coint outperformed pure trend modestly
- ML logistic: high variance across seeds — fragile

Known issues:
- Synthetic data only. Strategies that look good here may not survive real markets.

---

## v7 — 2025 — `versions/v7_gold_quant.py` — Honest Edition
Asset: GLD.JO
Improved:
- Reduced feature set 13 → 5 (ema_trend, momentum, rv, vov, coint_z)
- Signal score replaced hand-weighted sum with regularized logistic regression
- Buy-and-hold benchmark mandatory in output
- Net-of-friction returns (gross AND net Sharpe both reported)
- Regime confidence scales Kelly (prevents whipsaw near regime boundaries)
- Position tracking from cumulative fills (no more silent short zeroing)
- Filled orders contribute to position, not just OPEN/PENDING
- Walk-forward friction applied inside OOS return

Fixed (vs v6):
- Position tracking bug (double-counted some orders)
- Walk-forward pre-cost Sharpe was inflated by ~0.5–1.0
- `actual_return` backfill (without it, Kupiec/Christoffersen tests never fired)
- GARCH rescale sanity check on output

Removed:
- News sentiment (too noisy at daily cadence)
- RSI as standalone feature (overfit prone)
- Intraday gap (no quality data at JSE retail level)

Known issues:
- Still on synthetic calibrated data — see v7.2 for real-data verdict.

---

## v6 — 2025 — `versions/v6_gold_quant.py` & `versions/v6_botV6.py` — Two parallel v6 attempts
Asset: GLD.JO + XAUUSD

`v6_gold_quant.py`:
- Institutional wrapper (Goldman ensemble ML + Schwab position limits + IB TWS structure)
- Heavy architecture obscures signal quality

`v6_botV6.py`:
- EquityCurveEngine: daily NAV + drawdown tracking
- Paper trading engine with order audit trail
- Manual CLI confirmation gate before any "live" order

Fixed:
- GARCH rescale=True addresses v5 yfinance multi-ticker KeyError

Known issues:
- Pre-friction Sharpe headlines (caught & fixed in v7)
- 13+ hand-weighted features (caught & cut in v7)
- HMM posterior confidence ignored in sizing (caught & fixed in v7)
- Daily refit of GARCH/HMM (slow + unstable)

---

## v5 — `versions/v5_monte_carlo.py` — Walk-forward + modular OOP
Asset: GLD.JO
Improved:
- Walk-forward expanding-window backtest with OOS Sharpe tracking
- Modular OOP: DataFeed, RiskEngine, SignalEngine, ExecutionEngine, BacktestEngine
- First version to eliminate look-ahead bias structurally

Known issues:
- Computationally expensive
- No equity curve visualization
- Multi-ticker yfinance KeyError (fixed in v6)

> Note: the `v5_1` upload provided to this project was byte-identical to v5
> (verified by MD5). The v5.1 entry has been collapsed into v5; if a true
> v5.1 file exists, re-upload it and add a separate entry.

---

## v4 — `versions/v4_monte_carlo.py` — Foundation: HMM + EVT + GARCH-t + fractional Kelly
Asset: GLD.JO
Improved:
- HMM regime detection (Gaussian, 3 states: bull/calm/crisis)
- GARCH(1,1) with Student-t innovations
- Extreme Value Theory CVaR (Generalized Pareto)
- Bootstrap Sharpe confidence interval
- Fractional Kelly (×0.25) — first version to size sensibly

Known issues:
- No walk-forward — assumes static parameters across entire backtest → look-ahead bias
- Monolithic script (no OOP)

---

## v2 — `versions/v2_gold_bot.py` — Foundation script
Asset: GLD.JO
Improved:
- EMA crossover, RSI (Wilder), Monte Carlo (GBM), GARCH(1,1), naive Kelly, parametric VaR
- Macro basket: ZAR, DXY, VIX
- Telegram alerts
- Daily schedule at 17:30 SAST

Fixed:
- ZAR=X added (v1 only had USD-priced gold, mispricing GLD.JO)

Known issues (most fixed in later versions):
- All-or-nothing feature attribution — if trade won, all weights moved up regardless of which signal was right. Fixed forward.
- No regime detection
- No walk-forward
- 8 hand-weighted features
- Hard-coded ticker
