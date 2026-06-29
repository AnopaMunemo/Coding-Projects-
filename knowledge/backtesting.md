# Backtesting & Validation Knowledge Base

How strategies are validated before any consideration of live capital. The guiding principle: **prove the edge out-of-sample; never deploy on backtest hope alone.**

---

## 1. Walk-Forward Analysis

- **Train** on a window (e.g., 252 trading days).
- **Test out-of-sample** on the next window (e.g., 63 days).
- **Slide forward without overlap** between successive test windows.
- **Never use look-ahead data** — no information from the future may leak into the training or signal at decision time.

---

## 2. Profitability Thresholds (Minimums for Live)

A strategy must clear **all** of these before it is even a live candidate:

| Metric | Minimum |
| ------ | ------- |
| Profit factor | >= 1.5 |
| Sharpe ratio | >= 0.5 |
| Win rate | >= 35% |
| Max drawdown | < 30% |
| Return / drawdown ratio | >= 2.0 |
| Trades per 100 days | between 5 and 50 |

---

## 3. Overfitting Prevention

- **Parameter sensitivity:** Vary parameters **±20%** — performance should hold, not collapse.
- **Trade-frequency sanity check:** Too few or too many trades is a red flag (see the 5–50 per 100 days band above).
- **Monte Carlo simulation:** e.g., 1000 runs; check the **probability of ruin**.
- **Out-of-sample testing:** The core defense — see walk-forward above.
- **Consistency of Sharpe across walk-forward windows:** If **std/mean > 0.5**, treat it as an overfitting risk (results are too inconsistent to trust).

---

## 4. CRITICAL HONEST LESSON

In the **2021 dissertation**, the **Perfect Order Strategy** was backtested on **7 FX pairs over 2005–2020** (MetaTrader 5, daily bars, $100k account, 1:100 leverage).

**It LOST money on 6 of the 7 pairs:**

| Pair | Result |
| ---- | ------ |
| GBP/USD | **+12.1%** (only winner) |
| EUR/USD | −16.9% |
| AUD/USD | −20.6% |
| NZD/USD | −26.3% |
| USD/CHF | −18.5% |
| USD/CAD | −7.1% |
| USD/JPY | −12.0% |

- **Win rates were 20–35%.**
- The **risk model rejected 30–43% of signals** (mostly due to invalid stop placement).

> **Takeaway:** A well-known, well-defined strategy is **NOT** automatically profitable. The edge must be proven out-of-sample; **risk management and validation matter more than the signal.** Never deploy real capital on backtest hope alone.

---

## 5. Metrics Formulas

| Metric | Formula |
| ------ | ------- |
| Win Rate | wins / total |
| Profit Factor | gross profit / \|gross loss\| |
| Sharpe Ratio | (avg return − risk-free) / stdev |
| Equity Drawdown | (peak − trough) / peak |
