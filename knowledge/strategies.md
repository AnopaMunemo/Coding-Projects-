# Trading Strategies Knowledge Base

This document distills the trading strategies used and studied in this project. Sources include a 2021 NOVA Masters dissertation and general trading literature.

---

## 1. Perfect Order Strategy (Trend-Following)

**Source:** 2021 NOVA Masters dissertation.
**Timeframe:** Daily.

A trend-following system built on the alignment of five simple moving averages.

### Moving Averages

Five MAs with periods: **10, 20, 50, 100, 200**.

### "Perfect Order" Alignment

The MAs must align in strict order:

| Regime | Condition |
| ------ | --------- |
| **Bull** | Fast above slow, ascending: MA10 > MA20 > MA50 > MA100 > MA200 |
| **Bear** | Fast below slow, descending: MA10 < MA20 < MA50 < MA100 < MA200 |

### Confirmation — The "Trade Tripod"

A signal is only valid when all three legs hold:

1. **MAs aligned** in perfect order (bull or bear).
2. **ADX >= 20** (trend strength filter).
3. **5-bar persistence** — alignment persists for 5 consecutive daily bars.

### Entry, Exit, and Stops

- **Exit:** Close when **MA10 crosses MA20 in the opposite direction**.
- **Stop-loss (long):** Lowest low of the 5 bars before entry.
- **Stop-loss (short):** Highest high of the 5 bars before entry.

> Note: This strategy's live backtest results were sobering — see `backtesting.md` for the honest lesson on out-of-sample performance.

---

## 2. General Trend-Following

- Markets trend only **~20% of the time** (Burns, 2014). The other ~80% is range-bound or noise.
- **Trade with the direction** of the prevailing trend.
- Profitability depends on **wins exceeding losses** — average win size and win frequency together must overcome the cost of frequent small losses during non-trending periods.

---

## 3. Mean Reversion / Contrarian

- Prices oscillate around a **center of gravity** (a fair-value or moving average level).
- **Trade short-term deviations** away from that center, expecting reversion back toward it.
- Rooted in **statistical-arbitrage convergence** — pairs/baskets that have diverged tend to re-converge.

---

## 4. Value / Yield

- **Carry trade:** Long the high-yield currency, short the low-yield currency, to capture the interest-rate differential.
- **P/E and earnings yield:** Use the **earnings yield = E/P** (earnings over price) rather than P/E. This avoids divide-by-zero problems when earnings are near zero and gives a directly comparable yield figure.

---

## 5. Smart Money Concepts (SMC)

Price-action concepts implemented in the repo's `strategies.py`:

- **Order blocks** — zones of institutional accumulation/distribution.
- **Fair value gaps (FVG)** — price inefficiencies / imbalances left by fast moves.
- **Break of structure (BOS)** — confirmation that the prevailing trend structure has shifted.
- **Liquidity sweeps** — moves that take out stops/liquidity before reversing.

---

## 6. System Architecture

Signals flow through a four-stage pipeline. Each stage has a single responsibility:

```
Alpha Model  →  Risk Model  →  Execution Model  →  Position Management
 (signals)      (validate +     (fills)             (closes on MA
                size, reject                          break or SL)
                invalid)
```

| Stage | Responsibility |
| ----- | -------------- |
| **Alpha Model** | Generates trade signals from strategies (Perfect Order, mean reversion, SMC, etc.). |
| **Risk Model** | Validates the trade, sizes the position, sets the stop-loss, and **rejects invalid trades**. |
| **Execution Model** | Fills the order. |
| **Position Management** | Closes positions on an MA break or when the stop-loss is hit. |

See `risk_management.md` for the rejection logic and `backtesting.md` for validation thresholds.
