# 🛰️ Atlas Capital — Portfolio & Forex Desk

An institutional-grade, **dark-mode dashboard** that builds investment portfolios
and generates forex trade signals — localised for a **South African** user
(everything in Rand, default budget **R300**, adjustable on screen).

> **Educational decision-support tool — not licensed financial advice.**

---

## ✨ What it does

| Area | Capability |
|------|------------|
| **Stocks** | Builds a portfolio from your **budget, risk appetite, time horizon, and preferred stock type** (Tech / Value / Dividend / Emerging / Balanced). Screens for **undervalued** stocks using fundamentals (P/E, P/B, ROE, Graham Number). |
| **Probability** | Monte-Carlo engine answers *"Hold for **8 months** → **42% likelihood** of a **20%+** gain"* and renders a full probability matrix. |
| **Fixed income** | Pulls **Yield to Maturity (YTM)** from bond ETFs + the US Treasury yield curve; tilts allocation defensively when the curve inverts. |
| **Forex** | Session-timed signals with an **entry time range, exit time range, Stop Loss, Take Profit**, and **Recovery Sizing** that always aims to make back prior losses (safely capped). |
| **Reporting** | One-click **PDF report** for your supervisor + **export to MetaTrader 5** (MQL5 EA bridge). |

---

## 🏛️ Architecture

```
                        ┌──────────────────────────────┐
                        │         app.py (UI)          │
                        │   Streamlit · dark cinematic  │
                        │   ZAR budget · loading states │
                        └───────────────┬──────────────┘
                                        │
        ┌───────────────────────────────┼───────────────────────────────┐
        │                               │                               │
        ▼                               ▼                               ▼
┌────────────────┐          ┌────────────────────┐          ┌────────────────────┐
│  data_feed.py  │          │portfolio_optimizer │          │   forex_engine.py  │
│                │          │        .py         │          │                    │
│ • equities     │  bundle  │ • HMM regime detect│  signals │ • ATR SL/TP        │
│ • bond YTM     │ ───────► │ • Sharpe optimise  │ ───────► │ • session windows  │
│ • FX (D/H/5m)  │          │ • walk-forward     │          │ • recovery sizing  │
│ • yfinance API │          │ • Monte Carlo      │          │ • walk-forward bt  │
└────────────────┘          └────────────────────┘          └─────────┬──────────┘
                                        │                              │
                                        ▼                              ▼
                              ┌──────────────────┐          ┌────────────────────┐
                              │    report.py     │          │ signal_export.py   │
                              │  PDF for supervis│          │  → atlas_signals.csv│
                              └──────────────────┘          └─────────┬──────────┘
                                                                      │ file handshake
                                                                      ▼
                                                          ┌────────────────────────┐
                                                          │ mql5_bridge/           │
                                                          │   AtlasForexEA.mq5     │
                                                          │ (live execution in MT5)│
                                                          └────────────────────────┘
```

### Why these languages?

- **Python** — all data, analytics, optimisation, backtesting, and the dashboard.
- **MQL5 (MetaTrader 5)** — *live forex order execution only.* Python proposes
  signals; the EA owns the broker connection, risk checks, and order placement.
  (For sub-millisecond HFT you'd drop to **C++ with a FIX adapter** — not needed here.)

---

## 📁 Files

| File | Purpose |
|------|---------|
| `app.py` | The Streamlit dashboard (run this) |
| `data_feed.py` | Market data pipeline (equities, bonds, FX) via yfinance |
| `portfolio_optimizer.py` | Regime detection, Sharpe optimisation, Monte Carlo, walk-forward |
| `forex_engine.py` | Forex signals, ATR SL/TP, recovery position sizing |
| `report.py` | One-click PDF report generator |
| `signal_export.py` | Writes signals to CSV/JSON for MetaTrader 5 |
| `mql5_bridge/AtlasForexEA.mq5` | MetaTrader 5 Expert Advisor (live execution) |
| `requirements.txt` | All Python dependencies |
| `SETUP.md` | **Step-by-step setup for any desktop** |

---

## 🚀 Quick start

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open `http://localhost:8501`. **Full setup walkthrough → [SETUP.md](SETUP.md).**

> **Tip:** On first run, leave *Live market data* **off** for an instant demo,
> then switch it **on** and click **⚡ Generate Strategy** for real prices.

---

## 🧠 The serious quant bits

- **Regime detection** — 3-state Gaussian **Hidden Markov Model** (Bull / Bear /
  Sideways) on return + volatility features, with a volatility-quantile fallback.
- **Optimisation** — Sharpe-maximising **mean-variance** (SLSQP) with a
  regime-shrinkage overlay and a risk-parity fallback; long-only, capped weights.
- **Validation** — expanding-window **walk-forward** (out-of-sample) reporting
  Sharpe, win rate, and max drawdown.
- **Probability** — bootstrap **Monte-Carlo** (10,000 paths) → P(≥target), median, P10, P90.
- **Recovery sizing** — controlled-Martingale: each post-loss trade is sized so a
  single win recovers the deficit + base profit, **hard-capped at ×3.0** with a
  **15% drawdown circuit-breaker** to prevent account wipe-out.

---

## ⚠️ Disclaimer

Atlas Capital is for **education and research**. It is **not** financial advice.
Market data via `yfinance` (free, delayed). Simulated and past performance does
not guarantee future results. Trade only with capital you can afford to lose.
