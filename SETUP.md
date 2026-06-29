# 🛠️ Setup Guide — Run Atlas Capital on Any Desktop

This guide takes you from **nothing installed** to a **running dashboard**, even
on a brand-new computer. You only need **VS Code** and **Python**. Follow it
top to bottom — every command is copy-paste ready.

> ⏱️ **Time:** ~15–30 minutes the first time (most of it is downloads).

---

## 📋 What you need

| Requirement | Why | Link |
|-------------|-----|------|
| **Python 3.10 – 3.12** | Runs the whole app | https://www.python.org/downloads/ |
| **VS Code** | Your editor + terminal | https://code.visualstudio.com/ |
| **Internet** | First-time downloads + live prices | — |
| **(Optional) MetaTrader 5** | Only for *live* forex execution | https://www.metatrader5.com/ |

> ✅ On the Python installer's first screen, **tick "Add Python to PATH"**.
> This one checkbox prevents 90% of beginner problems.

---

## 1️⃣ Get the project onto the new desktop

**Option A — you have the folder (USB / zip / cloud):**
Copy the whole project folder (the one containing `app.py`) to the new desktop.
You can **delete** these before copying to save space — they regenerate:
`venv/`, `__pycache__/`, `mt5_signals/`.

**Option B — from GitHub:**
```bash
git clone <your-repo-url>
cd Coding-Projects-
git checkout claude/portfolio-forex-data-pipeline-C5QZK
```

---

## 2️⃣ Open it in VS Code

1. Launch **VS Code**.
2. `File → Open Folder…` → select the project folder (the one with `app.py`).
3. Open the built-in terminal: **`Ctrl + ~`** (that's the key above Tab).
   - If prompted *"Do you trust the authors?"* → **Yes**.

You should see a terminal at the bottom, sitting inside the project folder.

---

## 3️⃣ Confirm Python works

In the terminal, type:

```bash
python --version
```

You should see something like `Python 3.11.x`.

> 🪟 **Windows:** if `python` says "not found", try `py --version`. If that works,
> use `py` instead of `python` for every command below.
> 🍎 **Mac/Linux:** if `python` fails, use `python3` everywhere instead.

---

## 4️⃣ Create a virtual environment (keeps things tidy)

A "venv" is a private sandbox for this project's libraries so they don't clash
with anything else on the computer.

```bash
python -m venv venv
```

**Activate it** (do this every time you open a new terminal for this project):

| OS | Command |
|----|---------|
| **Windows (PowerShell)** | `venv\Scripts\Activate.ps1` |
| **Windows (Command Prompt)** | `venv\Scripts\activate.bat` |
| **Mac / Linux** | `source venv/bin/activate` |

✅ When active, your terminal line starts with **`(venv)`**.

> 🪟 **Windows PowerShell error** ("running scripts is disabled")? Run this once:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```
> Type `Y`, press Enter, then activate again.

---

## 5️⃣ Install all the libraries (one command)

```bash
pip install -r requirements.txt
```

This reads `requirements.txt` and installs everything: Streamlit, pandas, numpy,
scipy, yfinance, plotly, reportlab, and hmmlearn. ☕ Give it 2–5 minutes.

> If `hmmlearn` fails to install, **ignore it** — the app automatically falls
> back to a built-in regime detector. Everything else still works.

---

## 6️⃣ Run the dashboard 🎉

```bash
streamlit run app.py
```

Your browser opens automatically at **`http://localhost:8501`**.
If it doesn't, copy that address from the terminal into your browser.

**First-run tip for a smooth demo:**
1. In the sidebar, leave **🔴 Live market data → OFF** → the app shows instantly
   with realistic synthetic data (perfect for a quick walkthrough).
2. Then flip it **ON** and click **⚡ Generate Strategy** to pull real prices
   (takes ~20–60 seconds the first time).

**To stop the app:** click the terminal and press **`Ctrl + C`**.

---

## 7️⃣ Using the dashboard

| Control (sidebar) | What it does |
|-------------------|--------------|
| **Amount (ZAR)** | Your budget in Rand. Default **R300**. Type any amount or tap the quick buttons (R300 / R1k / R5k / R25k). |
| **Risk Appetite** | Conservative → more bonds; Aggressive → more stocks. |
| **Time Horizon** | How many months you'll hold. Feeds the probability engine. |
| **Preferred Stock Type** | Tech / Value / Dividend / Emerging / Balanced. |
| **Target Profit** | The % gain the probability engine reports your odds for. |
| **⚡ Generate Strategy** | Re-runs everything with your inputs. |

**Tabs:** 📊 Portfolio · 💱 Forex Desk · 🏦 Fixed Income.

**📄 Download PDF Report** (Portfolio tab) → a branded one-pager to share/present.
**📡 Export to MT5** (Forex tab) → writes signal files for MetaTrader 5 (see §8).

---

## 8️⃣ (Optional) Connect live forex execution — MetaTrader 5

The Python app **never places live trades** — it proposes signals. MetaTrader 5
executes them via the included Expert Advisor.

1. Install **MetaTrader 5** and log into your broker (a **demo account** first!).
2. In MT5: `File → Open Data Folder` → open `MQL5 → Experts`.
3. Copy **`mql5_bridge/AtlasForexEA.mq5`** into that `Experts` folder.
4. In MT5's **Navigator**, right-click → **Refresh**, then double-click
   **AtlasForexEA** onto a chart.
5. In the EA settings, **keep `InpDryRun = true`** at first — it *logs* trades
   without placing them, so you can verify everything safely.
6. Point the signal file to MT5: the EA reads from MT5's `MQL5/Files/` folder.
   Copy `mt5_signals/atlas_signals.csv` there after each **📡 Export to MT5**,
   or set your export folder to that path.

> 🔒 **Safety:** Only set `InpDryRun = false` on a **demo account** once you've
> watched the logs and trust the behaviour. The EA enforces broker lot limits,
> signal freshness, and the session entry window.

---

## 🆘 Troubleshooting

| Problem | Fix |
|---------|-----|
| `python: command not found` | Use `py` (Windows) or `python3` (Mac/Linux). Reinstall Python with **"Add to PATH"** ticked. |
| `streamlit: command not found` | Your venv isn't active (no `(venv)` in the prompt). Re-run the activate command from §4. |
| PowerShell won't activate venv | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once (§4). |
| `pip install` fails on `hmmlearn` | Ignore it — the app has an automatic fallback. |
| Charts are missing | `pip install plotly` |
| "PDF unavailable" | `pip install reportlab` |
| App loads but no live data | Yahoo Finance may be rate-limiting — wait a minute, or use synthetic mode (toggle off). |
| Browser didn't open | Manually visit `http://localhost:8501`. |
| Port already in use | `streamlit run app.py --server.port 8502` |

---

## 🔁 Daily use (after first setup)

Every time you come back, it's just three steps:

```bash
# 1. activate the sandbox  (Windows: venv\Scripts\Activate.ps1)
source venv/bin/activate
# 2. run
streamlit run app.py
# 3. stop with Ctrl + C when done
```

That's it — you're running an institutional-grade desk from your laptop. 🚀
