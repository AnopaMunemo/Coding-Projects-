# 📘 Quantum Horizon Engine — Complete MT5 Setup Guide

A step-by-step, click-by-click walkthrough for installing the EA, compiling it,
and configuring Stop Loss / Take Profit / Entry for every trade.

---

## PART 1 — INSTALL METATRADER 5

### Step 1.1 — Download MT5
1. Go to your broker's website (recommended for South Africa: **Exness**, **IC Markets**, **FP Markets**, or **HotForex** — all support ZAR accounts and micro lots).
2. Download **MetaTrader 5** (NOT MT4 — this EA is MQL5 only).
3. Install and log in with your broker account credentials.

```
┌─────────────────────────────────────────────┐
│  MetaTrader 5 Login                          │
│  ┌─────────────────────────────────────┐    │
│  │ Login:    12345678                   │    │
│  │ Password: ••••••••••                 │    │
│  │ Server:   Exness-MT5Real            │    │
│  └─────────────────────────────────────┘    │
│            [ Login ]                          │
└─────────────────────────────────────────────┘
```

---

## PART 2 — INSTALL THE EA FILE

### Step 2.1 — Open the Data Folder
In MT5, click the top menu:

```
File  ►  Open Data Folder
```

```
┌─ File ──────────────────┐
│ New Chart               │
│ Open an Account         │
│ ─────────────────────── │
│ Open Data Folder    ◄── click this
│ ─────────────────────── │
│ Exit                    │
└─────────────────────────┘
```

A Windows Explorer window opens. Navigate into:

```
MQL5  ►  Experts
```

Full path looks like:
```
C:\Users\YourName\AppData\Roaming\MetaQuotes\Terminal\<long-id>\MQL5\Experts\
```

### Step 2.2 — Copy the EA file
Drag and drop (or copy-paste) `Quantum_Horizon_Engine.mq5` into the **Experts** folder.

```
📁 MQL5
   📁 Experts
      📄 Quantum_Horizon_Engine.mq5   ◄── put it here
   📁 Indicators
   📁 Scripts
```

---

## PART 3 — COMPILE THE EA

### Step 3.1 — Open MetaEditor
Back in MT5, press **F4** (or click the MetaEditor icon in the toolbar).

### Step 3.2 — Find and open the file
In MetaEditor's left **Navigator** panel:

```
📂 Navigator
   📂 Experts
      📄 Quantum_Horizon_Engine.mq5   ◄── double-click
```

### Step 3.3 — Compile
Press **F7** (or click the **Compile** button at the top).

Watch the bottom **Errors** tab:

```
┌─ Toolbox ──────────────────────────────────────┐
│ Errors  │ 0 error(s), 0 warning(s)   ◄── SUCCESS│
└────────────────────────────────────────────────┘
```

✅ **If you see "0 errors, 0 warnings" — you're ready.**
❌ If you see errors, make sure you copied the entire file and MT5 is updated to the latest build.

---

## PART 4 — ATTACH THE EA TO A CHART

### Step 4.1 — Open a chart
In MT5, open a chart for the pair you want to trade.

**Recommended for a R300 / ~$16 account:**
- `EURUSD` or `GBPUSD` (tight spreads, low cost)
- Avoid `XAUUSD` (Gold) and `NAS100` at first — they move fast and need bigger accounts.

```
View ► Market Watch ► right-click EURUSD ► Chart Window
```

### Step 4.2 — Set the timeframe
Click **M15** in the top toolbar (this matches `ExecTimeframe`):

```
[ M1 ] [ M5 ] [ M15◄ ] [ M30 ] [ H1 ] [ H4 ] [ D1 ]
```

### Step 4.3 — Drag the EA onto the chart
In MT5's **Navigator** (Ctrl+N):

```
📂 Navigator
   📂 Expert Advisors
      🤖 Quantum_Horizon_Engine   ◄── drag this onto the chart
```

---

## PART 5 — CONFIGURE TRADE SETTINGS (SL / TP / ENTRY)

When you drop the EA on the chart, a settings window appears. Click the **Inputs** tab.

### Step 5.1 — Common tab (enable trading)
```
┌─ Common ──────────────────────────────────┐
│ ☑ Allow Algo Trading                       │
│ ☑ Allow modification of Signal settings    │
└────────────────────────────────────────────┘
```

### Step 5.2 — Inputs tab — KEY SETTINGS FOR R300

Scroll to each group and set these values:

#### === PLUS ENGINE TRADE MANAGEMENT ===
| Setting | Value | What it does |
|---------|-------|--------------|
| `EntryType` | `ENTRY_MARKET` | Enter instantly at market price |
| `LotMethod` | `LOT_RISK_PERCENT` | Auto-sizes lot from risk % |
| `RiskPercent` | `0.5` | Risk only 0.5% (~R1.50) per trade |
| `SL_Mode` | `SL_ATR_MULTIPLE` | Stop Loss adapts to volatility |
| `ATR_MultSL` | `1.5` | SL = 1.5 × ATR below entry |
| `TP_Mode` | `TP_RISK_REWARD` | Take Profit = reward ratio |
| `RiskRewardRatio` | `2.0` | TP is 2× the SL distance |
| `StealthSLTP` | `true` | Hides SL/TP from broker (anti stop-hunt) |

#### How SL/TP are calculated per trade (automatic):

```
        ENTRY (Buy @ 1.08500)
              │
   ATR(14) = 0.00120 (12 pips)
              │
   ┌──────────┴──────────┐
   │                     │
 STOP LOSS            TAKE PROFIT
 1.5 × ATR            2.0 × SL distance
 = 18 pips below      = 36 pips above
 = 1.08320            = 1.08860
   │                     │
 Risk: R1.50         Reward: R3.00
   └─────── 1 : 2 ───────┘
```

**You don't manually set SL/TP per trade — the EA calculates them automatically** from ATR (volatility) every time it opens a position. You only set the *rules* once (above).

#### === FILTERS & SAFETY ===
| Setting | Value |
|---------|-------|
| `MaxSpreadPts` | `30` |
| `BlockRollover` | `true` |
| `MaxSymbolTrades` | `2` |

#### === RISK MANAGEMENT ===
| Setting | Value |
|---------|-------|
| `MaxDailyDD` | `3.0` |
| `UsePropFirmMode` | `true` |
| `MagicNumber` | `202500` |

### Step 5.3 — Click OK
The EA loads. You'll see the dashboard appear top-left of the chart.

---

## PART 6 — ENABLE LIVE TRADING

### Step 6.1 — Master Algo Trading button
Click the **Algo Trading** button in the MT5 top toolbar. It must turn **green**:

```
[ ⏵ Algo Trading ]   ◄── GREEN = ON
```

### Step 6.2 — Confirm the smiley face
Top-right corner of the chart shows the EA status:

```
🙂  = EA active, trading allowed     ✅
😞  = Algo Trading is OFF            ❌ (click the button)
```

### Step 6.3 — Read the dashboard
```
┌─ QUANTUM HORIZON ENGINE v2.0 ──────┐
│ REGIME          SIGNAL              │
│ TRENDING ▲      BUY                 │
│ CONFIDENCE      SESSION             │
│ 72%             London/NY Overlap   │
│ ──── PERFORMANCE ────               │
│ WIN RATE        PROF.FACTOR         │
│ 58%             1.45                │
│ DAILY P&L       TOTAL P&L           │
│ +2.10           +12.40              │
│ ──── RISK STATUS ────               │
│ STATUS: ACTIVE                      │
│ [CLOSE ALL][PAUSE][BUY][SELL]       │
└─────────────────────────────────────┘
```

---

## PART 7 — TEST FIRST (STRATEGY TESTER)

**Always backtest before risking real money.**

### Step 7.1 — Open the Strategy Tester
Press **Ctrl+R** or:
```
View ► Strategy Tester
```

### Step 7.2 — Configure
```
┌─ Strategy Tester ──────────────────────────┐
│ Expert:   Quantum_Horizon_Engine           │
│ Symbol:   EURUSD                            │
│ Period:   M15                               │
│ Date:     Last 6 months                     │
│ Model:    Every tick based on real ticks    │
│ Deposit:  $16  (or 300 ZAR)                 │
│ Leverage: 1:500                             │
└─────────────────────────────────────────────┘
          [ Start ]
```

### Step 7.3 — Read the results
After it runs, check the **Backtest** tab:
- **Profit Factor** > 1.3 = good
- **Max Drawdown** < 20% = acceptable
- **Sharpe Ratio** > 1.0 = solid

If results look good on 6 months of history, try a **demo account** for 2–4 weeks before going live with the R300.

---

## ⚠️ IMPORTANT SAFETY NOTES

1. **Start on DEMO.** Never put real money in until you've watched it trade on a demo account for at least 2 weeks.
2. **R300 is a very small account.** Even with perfect risk management, growth is slow at first. Compounding 0.5% per trade is realistic; doubling overnight is not.
3. **The EA can lose money.** No algorithm wins every trade. The risk controls limit losses but cannot eliminate them.
4. **Broker matters.** You need micro-lots (0.01) and tight spreads. Exness offers cent accounts ideal for small balances.

---

## QUICK TROUBLESHOOTING

| Problem | Fix |
|---------|-----|
| EA shows 😞 face | Click green "Algo Trading" button |
| No trades opening | Check dashboard — regime may be "SIDEWAYS" (it waits for trends) |
| "Not enough money" error | Lower `RiskPercent` or use a cent account |
| Compile errors | Update MT5 to latest build, re-copy the full file |
| Dashboard not showing | Set `ShowDashboard = true` in Inputs |
