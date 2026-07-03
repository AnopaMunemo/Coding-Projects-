# Risk Management Knowledge Base

Risk management is treated as more important than signal generation in this project. The Risk Model validates and sizes every trade and rejects anything that violates the rules below.

---

## 1. Position Sizing

### Basso (2019) Formula

```
lotSize = (riskSize% × accountBalance) / (SLpips × pipValue)
```

**Worked example:**

| Input | Value |
| ----- | ----- |
| Account balance | $100,000 |
| Risk per trade | 1% |
| Stop-loss | 100 pips |
| Pip value | $10/pip |
| **Result** | **1 lot** |

Calculation: (0.01 × 100,000) / (100 × 10) = 1,000 / 1,000 = **1 lot**.

### Fractional Kelly

```
kelly = winRate − (1 − winRate) / (avgWin / avgLoss)
```

- Use **1/4 Kelly (0.25 fraction)** of the computed value.
- **Never use full Kelly** — it is too aggressive and risks deep drawdowns.

---

## 2. Hard Caps (Frozen Config)

These limits come from this repo's frozen configuration and are non-negotiable safety bounds.

| Parameter | Limit |
| --------- | ----- |
| MAX_POSITION_SIZE | 2% of equity |
| MAX_RISK_PER_TRADE | 1% |
| MAX_DAILY_LOSS | 5% |
| MAX_HOURLY_LOSS | 2% |
| MAX_WEEKLY_LOSS | 8% |
| MAX_MONTHLY_LOSS | 12% |
| MAX_DRAWDOWN | 20% |
| MIN_MARGIN_RATIO | 50% |
| MAX_PORTFOLIO_EXPOSURE | 60% |
| ATR stop multiplier | 1.5 |
| ATR take-profit multiplier | 2.5 |

---

## 3. Stop-Loss Validation Rules

A trade is **rejected** if any rule fails:

- Stop-loss must be **> 0**.
- **Long:** stop-loss must be **below** entry (SL < entry).
- **Short:** stop-loss must be **above** entry (SL > entry).
- **One position per instrument** — no stacking on the same symbol.
- Reject if the computed **size exceeds the broker limit**.

---

## 4. Rejection Codes

| Code | Meaning |
| ---- | ------- |
| `position_already_open` | An open position already exists for this instrument. |
| `invalid_SL` | Stop-loss is missing or not greater than zero. |
| `SL_incompatible_with_entry` | SL is on the wrong side of entry (long SL>=entry, or short SL<=entry). |
| `invalid_volume` | Computed size is invalid or exceeds the broker's allowed volume. |

---

## 5. Martingale / Recovery Sizing

- A Martingale/recovery sizer exists in the `forex_engine` (the **RecoverySizer**).
- It is **OFF by default** because recovery sizing **conflicts with the hard-cap safety model** (it scales up exposure after losses, which can blow past the drawdown and exposure caps).
- See `decisions_log.md` for the standing decision to keep it disabled.
