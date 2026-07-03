# Atlas Quant Stack — Run Guide (VS Code)

Two parts that work together:

- **The Desk** (`app.py`) — Streamlit analysis brain (signals, charts, backtests, reports).
- **The Engine** (`trading_bot/`) — async execution bot (risk, brokers, circuit breakers).

Requires **Python 3.11+**.

---

## 1. One-time setup

```bash
# from the project root
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

In VS Code: open the folder → bottom-right **Python interpreter** → pick `.venv`.

---

## 2. Verify everything works (do this first)

```bash
# Tests — should say "11 passed"
python -m pytest trading_bot/tests -q

# Production gate score — should say 100/100 (read EVALUATION.md for what that means)
python -m trading_bot.main --mode score

# A real backtest on the safe built-in strategy — should print metrics + "Validation passed: True"
python -m trading_bot.main --mode backtest --strategy swing --symbols AAPL --start 2022-01-01 --end 2024-01-01
```

---

## 3. Run the engine (paper / offline first — always safe)

```bash
python -m trading_bot.main --mode paper --broker paper --symbols AAPL GLD.JO XAUUSD
```
This runs the async trading loop against in-memory paper fills (no real orders).
Logs stream to `logs/atlas_swing_bot.log`; trades to `audit/`; state to `bot_state.json`.
Press **Ctrl-C** to stop.

### Choosing the strategy
- `--strategy swing` → built-in SMA/RSI/MACD (focused, currently the validated one).
- `--strategy ensemble` → the full Desk library (31 strategies) as one weighted signal.
  Tune it with the learning loop before trusting it (see §6).

---

## 4. Switching brokers ← (what you asked for)

The broker is chosen with **`--broker`**. Three options:

### a) Paper (default, offline simulation)
```bash
python -m trading_bot.main --mode paper --broker paper
```

### b) MT5 (your MetaTrader demo) — file bridge
```bash
python -m trading_bot.main --mode live --broker mt5
```
Writes orders to the MT5 `Common\Files` bridge that your `AtlasForexEA.mq5`
already reads. Keep MetaTrader open with the EA attached and Algo-Trading ON.
Use `--mode paper --broker mt5` for a dry run (logs orders, places none).

### c) Investec (live JSE / ZAR) — needs your API credentials
Set these environment variables first, then run:
```bash
# Windows (PowerShell)
$env:INVESTEC_CLIENT_ID="..."; $env:INVESTEC_CLIENT_SECRET="..."
$env:INVESTEC_API_KEY="..."; $env:INVESTEC_ACCOUNT_ID="..."

# macOS/Linux
export INVESTEC_CLIENT_ID=...   INVESTEC_CLIENT_SECRET=...
export INVESTEC_API_KEY=...     INVESTEC_ACCOUNT_ID=...

python -m trading_bot.main --mode live --broker investec
```
⚠️ Real money. Only after a successful demo period (see EVALUATION.md).

> You can also set the default permanently in `trading_bot/config.py`
> (`BotConfig.broker_type` and `strategy_type`). The CLI flag overrides it.

---

## 5. Run the Desk (analysis dashboard)

```bash
streamlit run app.py
```
Opens at http://localhost:8501 — portfolio builder, Forex & Gold desk, strategy lab,
risk engine, quant models, and the **Export to MT5** button that feeds the engine.

---

## 6. Keep it learning

- **Self-retraining bot:** the ensemble's per-strategy weights live in
  `state/ensemble_weights.json`. `trading_bot/learning/retrainer.py` rewards strategies
  that produced winning trades and penalises losers, and can walk-forward refit the ML
  models. Wire `Retrainer.update_from_outcomes(...)` into your end-of-day routine, or call
  `load_outcomes_from_audit()` to learn from the audit log.
- **AI context that persists across sessions:** `CLAUDE.md` + the `knowledge/` folder.
  Every time you (or Claude) make a decision or learn something, add it to
  `knowledge/decisions_log.md`. That is how the *project* keeps getting smarter even though
  the AI model itself doesn't retain memory between sessions.

---

## Troubleshooting
- `ModuleNotFoundError` → activate the venv and re-run `pip install -r requirements.txt`.
- Python too old → install 3.11+ (`python --version`).
- `yfinance` empty / offline → the Desk falls back to synthetic data; the engine paper mode
  still runs. Live data needs internet.
- Nothing prints in `--mode paper` → that's the infinite trading loop; check `logs/`.
