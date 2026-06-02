"""
app.py — Atlas Capital | Institutional Portfolio & Forex Dashboard
═══════════════════════════════════════════════════════════════════════════════

A cinematic, dark-mode Streamlit front-end that ties together:
    • data_feed.py            → live market data (equities, bonds, FX)
    • portfolio_optimizer.py  → regime-aware portfolio construction + Monte Carlo
    • forex_engine.py         → ATR signals + recovery position sizing

Localised for a South African user: all monetary values are shown in Rand (ZAR),
with a live USD/ZAR conversion and an adjustable budget (default R300).

Run:  streamlit run app.py
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import streamlit as st

# ── Plotly (graceful import) ──────────────────────────────────────────────────
try:
    import plotly.graph_objects as go
    import plotly.express as px
    _PLOTLY = True
except ImportError:  # pragma: no cover
    _PLOTLY = False

# ── Local engine imports (graceful) ───────────────────────────────────────────
_ENGINE_ERROR: Optional[str] = None
try:
    from data_feed import (
        DataFeedConfig, EquityConfig, FixedIncomeConfig, ForexConfig,
        DataFeedOrchestrator,
    )
    from portfolio_optimizer import PortfolioRequest, PortfolioOptimizer
    from forex_engine import run_forex_engine, ForexSignalEngine, ForexEngineConfig
    _ENGINES = True
except Exception as exc:  # pragma: no cover
    _ENGINES = False
    _ENGINE_ERROR = str(exc)

# Optional add-ons (graceful if their deps are missing)
try:
    from report import build_pdf_report
    _REPORT = True
except Exception:
    _REPORT = False

try:
    from signal_export import export_signals
    _SIGNAL_EXPORT = True
except Exception:
    _SIGNAL_EXPORT = False

logging.basicConfig(level=logging.WARNING)

# ══════════════════════════════════════════════════════════════════════════════
# Page config & cinematic theme
# ══════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Atlas Capital · Portfolio & Forex Desk",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)

CINEMATIC_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap');

:root {
    --bg-0:   #0a0e17;
    --bg-1:   #0f1626;
    --bg-2:   #161f33;
    --glass:  rgba(255,255,255,0.04);
    --stroke: rgba(255,255,255,0.08);
    --gold:   #f0b90b;
    --teal:   #2dd4bf;
    --green:  #34d399;
    --red:    #f87171;
    --blue:   #60a5fa;
    --txt-1:  #e8edf7;
    --txt-2:  #8b97ad;
}

/* App background — cinematic radial gradient */
.stApp {
    background:
        radial-gradient(1200px 600px at 15% -5%, rgba(45,212,191,0.08), transparent 55%),
        radial-gradient(1000px 500px at 95% 0%, rgba(240,185,11,0.07), transparent 50%),
        linear-gradient(180deg, var(--bg-0) 0%, var(--bg-1) 100%);
    color: var(--txt-1);
    font-family: 'Inter', sans-serif;
}

#MainMenu, footer, header {visibility: hidden;}
.block-container {padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1500px;}

/* Sidebar */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, var(--bg-1) 0%, var(--bg-0) 100%);
    border-right: 1px solid var(--stroke);
}
section[data-testid="stSidebar"] * { color: var(--txt-1); }

/* Hero header */
.hero {
    border: 1px solid var(--stroke);
    border-radius: 18px;
    padding: 26px 32px;
    margin-bottom: 22px;
    background:
        linear-gradient(135deg, rgba(45,212,191,0.10), rgba(240,185,11,0.05)),
        var(--glass);
    backdrop-filter: blur(14px);
    box-shadow: 0 8px 40px rgba(0,0,0,0.45);
}
.hero h1 {
    font-size: 2.05rem; font-weight: 800; letter-spacing: -0.5px;
    margin: 0; background: linear-gradient(90deg, #fff, var(--teal) 60%, var(--gold));
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.hero p { color: var(--txt-2); margin: 6px 0 0 0; font-size: 0.95rem; }
.hero .pill {
    display:inline-block; margin-top:12px; padding:4px 12px; border-radius:99px;
    font-size:0.72rem; font-weight:600; letter-spacing:0.5px;
    background: rgba(52,211,153,0.12); color: var(--green);
    border:1px solid rgba(52,211,153,0.3);
}

/* Metric cards */
.metric-card {
    border: 1px solid var(--stroke);
    border-radius: 16px;
    padding: 18px 20px;
    background: var(--glass);
    backdrop-filter: blur(10px);
    transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;
    height: 100%;
}
.metric-card:hover {
    transform: translateY(-3px);
    border-color: rgba(45,212,191,0.4);
    box-shadow: 0 10px 30px rgba(45,212,191,0.10);
}
.metric-card .label {
    font-size: 0.72rem; text-transform: uppercase; letter-spacing: 1px;
    color: var(--txt-2); font-weight: 600;
}
.metric-card .value {
    font-size: 1.85rem; font-weight: 800; margin-top: 6px;
    font-family: 'JetBrains Mono', monospace; line-height: 1.1;
}
.metric-card .sub { font-size: 0.78rem; color: var(--txt-2); margin-top: 4px; }
.v-gold  {color: var(--gold);}   .v-teal {color: var(--teal);}
.v-green {color: var(--green);}  .v-red  {color: var(--red);}
.v-blue  {color: var(--blue);}   .v-white{color: var(--txt-1);}

/* Section titles */
.section-title {
    font-size: 1.15rem; font-weight: 700; margin: 26px 0 14px 0;
    display:flex; align-items:center; gap:10px;
}
.section-title::before {
    content:''; width:4px; height:20px; border-radius:4px;
    background: linear-gradient(180deg, var(--teal), var(--gold));
}

/* Signal cards */
.sig-card {
    border:1px solid var(--stroke); border-radius:14px; padding:16px 18px;
    background: var(--glass); margin-bottom:14px; backdrop-filter: blur(8px);
}
.sig-card.long  {border-left:4px solid var(--green);}
.sig-card.short {border-left:4px solid var(--red);}
.sig-head {display:flex; justify-content:space-between; align-items:center;}
.sig-pair {font-size:1.15rem; font-weight:800; font-family:'JetBrains Mono',monospace;}
.badge {padding:3px 11px; border-radius:99px; font-size:0.72rem; font-weight:700; letter-spacing:0.5px;}
.badge.long  {background:rgba(52,211,153,0.15); color:var(--green); border:1px solid rgba(52,211,153,0.35);}
.badge.short {background:rgba(248,113,113,0.15); color:var(--red);   border:1px solid rgba(248,113,113,0.35);}
.badge.rec   {background:rgba(240,185,11,0.15);  color:var(--gold);  border:1px solid rgba(240,185,11,0.35);}
.sig-grid {display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-top:14px;}
.sig-cell .k {font-size:0.68rem; color:var(--txt-2); text-transform:uppercase; letter-spacing:0.5px;}
.sig-cell .val {font-size:0.98rem; font-weight:700; font-family:'JetBrains Mono',monospace; margin-top:2px;}

/* Recovery bar */
.rec-wrap {margin-top:14px; padding:12px 14px; border-radius:10px;
    background:rgba(240,185,11,0.06); border:1px solid rgba(240,185,11,0.2);}
.rec-bar-bg {height:8px; border-radius:99px; background:rgba(255,255,255,0.08); margin-top:8px; overflow:hidden;}
.rec-bar-fill {height:100%; border-radius:99px; background:linear-gradient(90deg,var(--gold),var(--red));}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {gap:6px; border-bottom:1px solid var(--stroke);}
.stTabs [data-baseweb="tab"] {
    background:transparent; border-radius:10px 10px 0 0; padding:10px 20px;
    color:var(--txt-2); font-weight:600;
}
.stTabs [aria-selected="true"] {
    background:var(--glass); color:var(--txt-1);
    border-bottom:2px solid var(--teal);
}

/* Dataframe tweaks */
.stDataFrame {border-radius:12px; overflow:hidden; border:1px solid var(--stroke);}

/* Buttons */
.stButton>button {
    background: linear-gradient(135deg, var(--teal), #1ba8a0);
    color:#04201d; font-weight:700; border:none; border-radius:10px;
    padding:10px 18px; transition: all .18s ease;
}
.stButton>button:hover {box-shadow:0 8px 24px rgba(45,212,191,0.35); transform:translateY(-1px);}
</style>
"""
st.markdown(CINEMATIC_CSS, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def metric_card(label: str, value: str, sub: str = "", color: str = "white") -> str:
    return f"""
    <div class="metric-card">
        <div class="label">{label}</div>
        <div class="value v-{color}">{value}</div>
        <div class="sub">{sub}</div>
    </div>
    """


def rand(amount) -> str:
    """Format a USD amount as ZAR string using the session rate."""
    if amount is None:
        return "R—"
    try:
        rate = st.session_state.get("usd_zar", 18.5)
        return f"R{float(amount) * rate:,.2f}"
    except (TypeError, ValueError):
        return "R—"


def rand_raw(zar_amount) -> str:
    if zar_amount is None:
        return "R—"
    try:
        return f"R{float(zar_amount):,.2f}"
    except (TypeError, ValueError):
        return "R—"


def _sf(value, spec: str, fallback: str = "—") -> str:
    """Safe formatter — returns fallback string instead of crashing on None/NaN."""
    if value is None:
        return fallback
    try:
        import math
        if isinstance(value, float) and math.isnan(value):
            return fallback
        return format(value, spec)
    except (TypeError, ValueError):
        return fallback


# ══════════════════════════════════════════════════════════════════════════════
# Cached data loaders
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(show_spinner=False, ttl=3600)
def get_usd_zar() -> float:
    """Live USD/ZAR exchange rate (falls back to a sane default)."""
    try:
        import yfinance as yf
        df = yf.download("ZAR=X", period="5d", interval="1d",
                         progress=False, auto_adjust=True)
        if not df.empty:
            val = float(df["Close"].dropna().iloc[-1])
            if 5 < val < 40:
                return val
    except Exception:
        pass
    return 18.5   # conservative fallback


@st.cache_data(show_spinner=False, ttl=1800)
def load_market_bundle(theme: str, depth: str = "standard") -> Dict[str, Any]:
    """
    Run the full data pipeline. Cached for 30 min so repeated dashboard
    interactions don't re-hit the API. Returns {} on hard failure.
    """
    if not _ENGINES:
        return {}

    # Map theme → a focused, fast equity universe (keeps demo snappy)
    theme_universe = {
        "Tech":     ["AAPL","MSFT","NVDA","GOOGL","META","AMD","AVGO","ORCL"],
        "Value":    ["BRK-B","JPM","XOM","CVX","JNJ","KO","PG","BAC"],
        "Dividend": ["KO","PEP","JNJ","PG","MCD","MMM","VZ","O"],
        "Emerging": ["EEM","VWO","INDA","EWZ","MCHI","EWY","EWT","EWJ"],
        "Balanced": ["AAPL","MSFT","JPM","JNJ","KO","XOM","V","GOOGL"],
    }.get(theme, ["AAPL","MSFT","JPM","JNJ","KO","XOM","V","GOOGL"])

    cfg = DataFeedConfig(
        equity=EquityConfig(tickers=theme_universe, historical_period="2y"),
        fixed_income=FixedIncomeConfig(
            bond_etfs={"SHY":"1-3Y Treasury","IEF":"7-10Y Treasury",
                       "TLT":"20+Y Treasury","AGG":"US Aggregate",
                       "LQD":"IG Corporate","HYG":"High Yield"},
            historical_period="1y",
        ),
        forex=ForexConfig(
            majors=["EURUSD=X","GBPUSD=X","USDJPY=X","AUDUSD=X","USDCAD=X"],
            crosses=["EURGBP=X","GBPJPY=X"],
            commodities=["XAUUSD=X"],
        ),
    )
    try:
        return DataFeedOrchestrator(cfg).run()
    except Exception as exc:
        logging.error("Pipeline failed: %s", exc)
        return {}


@st.cache_data(show_spinner=False, ttl=1800)
def load_benchmark() -> Optional[pd.Series]:
    """S&P 500 daily close for the buy-&-hold benchmark."""
    try:
        import yfinance as yf
        df = yf.download("^GSPC", period="2y", interval="1d",
                         progress=False, auto_adjust=True)
        if not df.empty:
            s = df["Close"]
            if isinstance(s, pd.DataFrame):
                s = s.iloc[:, 0]
            return s.dropna()
    except Exception:
        pass
    return None


# ── Synthetic fallback so the demo is never blank ────────────────────────────

def synthetic_bundle(theme: str) -> Dict[str, Any]:
    """Generate a realistic synthetic bundle if the live API is unavailable."""
    dates = pd.date_range(end=datetime.now(), periods=504, freq="B")
    rng = np.random.default_rng(7)

    def hist(seed: int, drift: float, vol: float, p0: float = 120.0) -> pd.DataFrame:
        r = np.random.default_rng(seed)
        price = p0 * np.cumprod(1 + r.normal(drift, vol, len(dates)))
        df = pd.DataFrame({
            "Open": price, "High": price*1.008, "Low": price*0.992,
            "Close": price, "Volume": r.uniform(1e6, 5e6, len(dates)),
        }, index=dates)
        df["Return"] = df["Close"].pct_change()
        return df

    eq_tickers = {
        "Tech":["AAPL","MSFT","NVDA","GOOGL","META","AMD"],
        "Value":["BRK-B","JPM","XOM","JNJ","KO","PG"],
        "Dividend":["KO","PEP","JNJ","PG","MCD","VZ"],
        "Emerging":["EEM","VWO","INDA","EWZ","MCHI","EWY"],
        "Balanced":["AAPL","MSFT","JPM","JNJ","KO","XOM"],
    }.get(theme, ["AAPL","MSFT","JPM","JNJ","KO","XOM"])

    histories = {t: hist(i, 0.0005, 0.015, 80+10*i) for i, t in enumerate(eq_tickers)}
    screened = pd.DataFrame({
        "ticker": eq_tickers[:4],
        "currentPrice": [float(histories[t]["Close"].iloc[-1]) for t in eq_tickers[:4]],
        "trailingPE": [12.3, 14.8, 9.5, 17.1],
        "priceToBook": [1.8, 2.4, 1.1, 2.9],
        "returnOnEquity": [0.21, 0.18, 0.25, 0.15],
        "earningsYield": [0.081, 0.068, 0.105, 0.058],
        "grahamNumber": [float(histories[t]["Close"].iloc[-1])*1.2 for t in eq_tickers[:4]],
        "grahamMoS": [0.18, 0.12, 0.28, 0.09],
        "fcfYield": [0.06, 0.05, 0.08, 0.04],
        "compositeScore": [0.88, 0.81, 0.94, 0.72],
    })

    fi_tickers = ["SHY","IEF","TLT","AGG","LQD","HYG"]
    etf_hist = {t: hist(50+i, 0.0001, 0.004, 95+5*i) for i, t in enumerate(fi_tickers)}
    etf_yields = pd.DataFrame({
        "ticker": fi_tickers,
        "name": ["1-3Y Treasury","7-10Y Treasury","20+Y Treasury",
                 "US Aggregate","IG Corporate","High Yield"],
        "ytm_proxy": [0.0495, 0.0438, 0.0421, 0.0455, 0.0532, 0.0781],
        "ytm_source": ["sec_30d_yield"]*6,
        "duration_years": [1.9, 7.5, 17.2, 6.1, 8.4, 3.8],
    })

    fx_pairs = ["EURUSD=X","GBPUSD=X","USDJPY=X","AUDUSD=X","XAUUSD=X"]
    fx_daily = {}
    for i, t in enumerate(fx_pairs):
        base = 1.1 if "USD=X" in t and "JPY" not in t else (150 if "JPY" in t else 1950)
        r = np.random.default_rng(200+i)
        price = base * np.cumprod(1 + r.normal(0.0001, 0.006, len(dates)))
        d = pd.DataFrame({
            "Open": price, "High": price*1.004, "Low": price*0.996,
            "Close": price, "Volume": np.zeros(len(dates)),
        }, index=dates)
        d["Return"] = d["Close"].pct_change()
        fx_daily[t] = d

    opt_windows = pd.DataFrame({
        "mean_score": [0.84, 0.79, 0.73, 0.66],
        "pairs_count": [5,5,5,5],
        "sessions": ["⬡ LDN/NY Overlap","London","New York","Tokyo"],
    }, index=[13, 8, 15, 2])
    opt_windows.index.name = "utc_hour"

    return {
        "equity": {"histories": histories, "screened_universe": screened},
        "fixed_income": {
            "yield_curve": pd.Series({"3M":0.0521,"5Y":0.0432,"10Y":0.0418,"30Y":0.0445}),
            "curve_slope_bp": -103.0,
            "etf_yields": etf_yields,
            "etf_histories": etf_hist,
        },
        "forex": {"daily": fx_daily, "optimal_windows": opt_windows},
        "meta": {"run_at": datetime.now(timezone.utc).isoformat(), "synthetic": True},
    }


# ══════════════════════════════════════════════════════════════════════════════
# Sidebar — inputs
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown(
        "<div style='font-size:1.3rem;font-weight:800;'>🛰️ Atlas Capital</div>"
        "<div style='color:#8b97ad;font-size:0.8rem;margin-bottom:18px;'>"
        "Portfolio & Forex Desk</div>",
        unsafe_allow_html=True,
    )

    st.markdown("#### 💰 Investment Budget")

    if "budget_zar" not in st.session_state:
        st.session_state.budget_zar = 300.0   # default R300

    budget_zar = st.number_input(
        "Amount (ZAR)", min_value=50.0, max_value=10_000_000.0,
        value=float(st.session_state.budget_zar), step=50.0, format="%.2f",
        help="Your starting capital in South African Rand. Default R300.",
    )
    st.session_state.budget_zar = budget_zar

    # Quick top-up buttons
    qc = st.columns(4)
    for i, amt in enumerate([300, 1000, 5000, 25000]):
        if qc[i].button(f"R{amt:,}", key=f"quick_{amt}"):
            st.session_state.budget_zar = float(amt)
            st.rerun()

    st.divider()

    risk_appetite = st.select_slider(
        "🎯 Risk Appetite",
        options=["Conservative", "Moderate", "Aggressive"],
        value="Moderate",
    )
    time_horizon = st.slider(
        "⏳ Time Horizon (months)", min_value=1, max_value=60, value=8,
        help="How long you intend to hold the portfolio.",
    )
    stock_type = st.selectbox(
        "📈 Preferred Stock Type",
        ["Value", "Tech", "Dividend", "Emerging", "Balanced"],
    )
    target_return = st.slider(
        "🎁 Target Profit (%)", min_value=5, max_value=100, value=20, step=5,
        help="The probability engine reports your odds of hitting this.",
    ) / 100.0

    st.divider()
    use_live = st.toggle("🔴 Live market data", value=True,
                         help="Off = instant synthetic demo data (offline-safe).")
    run_btn = st.button("⚡ Generate Strategy", use_container_width=True)

    st.caption(f"USD/ZAR ≈ {get_usd_zar():.2f}  ·  {datetime.now():%Y-%m-%d %H:%M}")


# Persist the live rate for formatters
st.session_state.usd_zar = get_usd_zar()
usd_zar = st.session_state.usd_zar
budget_usd = budget_zar / usd_zar


# ══════════════════════════════════════════════════════════════════════════════
# Hero header
# ══════════════════════════════════════════════════════════════════════════════

regime_chip = st.session_state.get("last_regime", "—")
st.markdown(f"""
<div class="hero">
    <h1>Institutional Portfolio & Forex Desk</h1>
    <p>Regime-aware allocation · Monte-Carlo probability modelling · ATR forex signals with recovery sizing</p>
    <span class="pill">● LIVE · {datetime.now():%H:%M} · Budget {rand_raw(budget_zar)} (≈ ${budget_usd:,.2f})</span>
</div>
""", unsafe_allow_html=True)

if not _ENGINES:
    st.error(f"⚠️ Engine modules not importable: {_ENGINE_ERROR}\n\n"
             "Ensure data_feed.py, portfolio_optimizer.py and forex_engine.py "
             "are in the same folder as app.py.")
if not _PLOTLY:
    st.warning("Plotly not installed — charts disabled. Run: pip install plotly")


# ══════════════════════════════════════════════════════════════════════════════
# Run pipeline (with loading states)
# ══════════════════════════════════════════════════════════════════════════════

def build_everything() -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    with st.status("🛰️ Booting strategy engine…", expanded=True) as status:
        # 1. Data
        st.write("📡 Fetching market data (equities · bonds · FX)…")
        if use_live and _ENGINES:
            bundle = load_market_bundle(stock_type)
            if not bundle or not bundle.get("equity", {}).get("histories"):
                st.write("⚠️ Live feed empty — switching to synthetic demo data.")
                bundle = synthetic_bundle(stock_type)
        else:
            bundle = synthetic_bundle(stock_type)
        out["bundle"] = bundle

        # 2. Portfolio
        st.write("🧮 Optimising portfolio (regime detection · Sharpe · Monte Carlo)…")
        if _ENGINES:
            req = PortfolioRequest(
                budget_usd=budget_usd, risk_appetite=risk_appetite,
                time_horizon_months=time_horizon,
                preferred_stock_type=stock_type, target_return=target_return,
            )
            opt = PortfolioOptimizer(req, bundle)
            result = opt.build()
            months_axis = sorted({3, 6, time_horizon, 12, 18, 24})
            targets_axis = [0.05, 0.10, 0.15, 0.20, 0.30, 0.50]
            matrix = opt.probability_matrix(months_axis, targets_axis)
            out["result"] = result
            out["prob_matrix"] = matrix
            st.session_state.last_regime = result.regime

        # 3. Forex
        st.write("💱 Generating forex signals + recovery sizing…")
        if _ENGINES:
            signals, wf = run_forex_engine(
                bundle.get("forex", {}),
                account_equity=budget_usd, backtest=True,
            )
            out["signals"] = signals
            out["forex_wf"] = wf

        # 4. Benchmark
        st.write("📊 Loading S&P 500 benchmark…")
        out["benchmark"] = load_benchmark() if use_live else None

        status.update(label="✅ Strategy ready", state="complete", expanded=False)
    return out


# Trigger on first load or button press
if run_btn or "engine_out" not in st.session_state:
    st.session_state.engine_out = build_everything()

data = st.session_state.engine_out
bundle      = data.get("bundle", {})
result      = data.get("result")
prob_matrix = data.get("prob_matrix", pd.DataFrame())
signals     = data.get("signals", [])
forex_wf    = data.get("forex_wf", {})
benchmark   = data.get("benchmark")

if bundle.get("meta", {}).get("synthetic"):
    st.info("ℹ️ Showing **synthetic demo data** (live feed off or unavailable). "
            "Toggle *Live market data* on and click *Generate Strategy* for real prices.")


# ══════════════════════════════════════════════════════════════════════════════
# Tabs
# ══════════════════════════════════════════════════════════════════════════════

tab_port, tab_fx, tab_fi = st.tabs([
    "  📊  Portfolio  ", "  💱  Forex Desk  ", "  🏦  Fixed Income  "
])


# ──────────────────────────────────────────────────────────────────────────────
# TAB 1 — PORTFOLIO
# ──────────────────────────────────────────────────────────────────────────────

with tab_port:
    if result is None:
        st.warning("Run the engine to see portfolio output.")
    else:
        # ── Export bar ────────────────────────────────────────────────────
        exp_col1, exp_col2 = st.columns([3, 1])
        with exp_col2:
            if _REPORT:
                try:
                    pdf_bytes = build_pdf_report(
                        result=result, signals=signals,
                        fi_bundle=bundle.get("fixed_income", {}),
                        budget_zar=budget_zar, usd_zar=usd_zar,
                        forex_wf=forex_wf,
                    )
                    st.download_button(
                        "📄 Download PDF Report", data=pdf_bytes,
                        file_name=f"atlas_report_{datetime.now():%Y%m%d_%H%M}.pdf",
                        mime="application/pdf", use_container_width=True,
                    )
                except Exception as exc:
                    st.caption(f"PDF unavailable: {exc}")
            else:
                st.caption("Install reportlab for PDF export")

        # ── Headline metric cards ─────────────────────────────────────────
        c1, c2, c3, c4 = st.columns(4)
        regime_color = {"Bull":"green","Bear":"red","Sideways":"gold"}.get(result.regime,"white")
        c1.markdown(metric_card(
            "Market Regime", result.regime or "Unknown",
            f"{_sf(result.regime_confidence, '.0%')} model confidence", regime_color
        ), unsafe_allow_html=True)
        c2.markdown(metric_card(
            "Expected Return", _sf(result.expected_return, '.1%'),
            f"per annum · Sharpe {_sf(result.sharpe_ratio, '.2f')}", "teal"
        ), unsafe_allow_html=True)
        c3.markdown(metric_card(
            "Allocation Split",
            f"{_sf(result.equity_weight, '.0%')} / {_sf(result.fi_weight, '.0%')}",
            "equity / fixed income", "blue"
        ), unsafe_allow_html=True)
        _prob = result.monte_carlo_prob
        prob_color = "green" if (_prob is not None and _prob >= 0.6) else ("gold" if (_prob is not None and _prob >= 0.4) else "red")
        c4.markdown(metric_card(
            f"P(≥{target_return:.0%} profit)", _sf(_prob, '.0%'),
            f"holding {time_horizon} months", prob_color
        ), unsafe_allow_html=True)

        # ── Probability headline banner ───────────────────────────────────
        st.markdown(f"""
        <div class="hero" style="margin-top:18px;">
            <h1 style="font-size:1.5rem;">
            Hold for {time_horizon} months → {_sf(result.monte_carlo_prob, '.0%')} likelihood
            of a {target_return:.0%}+ gain</h1>
            <p>Median projected outcome: <b style="color:#34d399;">{_sf(result.mc_median_return, '+.1%')}</b>
            &nbsp;·&nbsp; downside (P10): <b style="color:#f87171;">{_sf(result.mc_p10, '+.1%')}</b>
            &nbsp;·&nbsp; upside (P90): <b style="color:#2dd4bf;">{_sf(result.mc_p90, '+.1%')}</b>
            &nbsp;·&nbsp; based on {result.request.monte_carlo_sims:,} Monte-Carlo paths</p>
        </div>
        """, unsafe_allow_html=True)

        # ── Probability matrix heatmap ────────────────────────────────────
        st.markdown('<div class="section-title">Probability Matrix · odds of hitting each target by hold time</div>',
                    unsafe_allow_html=True)
        if _PLOTLY and not prob_matrix.empty:
            z = prob_matrix.values * 100
            fig = go.Figure(data=go.Heatmap(
                z=z,
                x=[f"{m}m" for m in prob_matrix.columns],
                y=list(prob_matrix.index),
                colorscale=[[0,"#1a1f2e"],[0.4,"#7c5e10"],[0.7,"#caa017"],[1,"#34d399"]],
                text=[[f"{v:.0f}%" for v in row] for row in z],
                texttemplate="%{text}", textfont={"size":13,"family":"JetBrains Mono"},
                colorbar=dict(title="P(%)", tickfont=dict(color="#8b97ad")),
                hovertemplate="Hold %{x} · target %{y}<br>Probability: %{z:.0f}%<extra></extra>",
            ))
            fig.update_layout(
                template="plotly_dark", height=360,
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=10,r=10,t=10,b=10),
                xaxis_title="Holding period", yaxis_title="Target profit",
                font=dict(family="Inter", color="#e8edf7"),
            )
            st.plotly_chart(fig, use_container_width=True)
        elif prob_matrix.empty:
            st.info("Probability matrix unavailable (insufficient return history).")

        # ── Equity curve vs S&P 500 ───────────────────────────────────────
        st.markdown('<div class="section-title">Simulated Equity Curve · strategy vs S&P 500 buy & hold</div>',
                    unsafe_allow_html=True)
        if _PLOTLY:
            # Build weighted portfolio return series from allocations
            hist_map = bundle.get("equity", {}).get("histories", {})
            port_curve = None
            weights_sum = 0.0
            blended = None
            for a in result.allocations:
                if a.asset_class != "equity":
                    continue
                h = hist_map.get(a.ticker)
                if h is None or h.empty:
                    continue
                ret = h["Close"].pct_change().fillna(0)
                blended = ret * a.weight if blended is None else blended.add(ret * a.weight, fill_value=0)
                weights_sum += a.weight
            if blended is not None and weights_sum > 0:
                blended = blended / weights_sum  # normalise to equity sleeve
                port_curve = (1 + blended).cumprod()

            fig2 = go.Figure()
            if port_curve is not None:
                pv = port_curve / port_curve.iloc[0] * budget_zar
                fig2.add_trace(go.Scatter(
                    x=pv.index, y=pv.values, name="Atlas Strategy",
                    line=dict(color="#2dd4bf", width=2.5),
                    fill="tozeroy", fillcolor="rgba(45,212,191,0.08)",
                ))
            if benchmark is not None and not benchmark.empty:
                bench = benchmark / benchmark.iloc[0] * budget_zar
                fig2.add_trace(go.Scatter(
                    x=bench.index, y=bench.values, name="S&P 500 Buy & Hold",
                    line=dict(color="#f0b90b", width=2, dash="dot"),
                ))
            elif port_curve is not None:
                # synthetic benchmark if real one unavailable
                rng = np.random.default_rng(1)
                synth = pd.Series(
                    budget_zar * np.cumprod(1 + rng.normal(0.0004, 0.011, len(port_curve))),
                    index=port_curve.index)
                fig2.add_trace(go.Scatter(
                    x=synth.index, y=synth.values, name="S&P 500 (proxy)",
                    line=dict(color="#f0b90b", width=2, dash="dot")))

            fig2.update_layout(
                template="plotly_dark", height=380,
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=10,r=10,t=10,b=10),
                legend=dict(orientation="h", y=1.05, x=0, bgcolor="rgba(0,0,0,0)"),
                yaxis_title="Portfolio value (ZAR)",
                font=dict(family="Inter", color="#e8edf7"),
                hovermode="x unified",
            )
            fig2.update_yaxes(gridcolor="rgba(255,255,255,0.05)")
            fig2.update_xaxes(gridcolor="rgba(255,255,255,0.05)")
            st.plotly_chart(fig2, use_container_width=True)

        # ── Walk-forward stats ────────────────────────────────────────────
        if result.walk_forward and result.walk_forward.windows > 0:
            wf = result.walk_forward
            w1,w2,w3,w4 = st.columns(4)
            w1.markdown(metric_card("WF Windows", str(wf.windows), "out-of-sample", "white"), unsafe_allow_html=True)
            w2.markdown(metric_card("OOS Win Rate", _sf(wf.win_rate, '.0%'), "positive months", "green"), unsafe_allow_html=True)
            w3.markdown(metric_card("OOS Sharpe", _sf(wf.mean_oos_sharpe, '.2f'), "risk-adjusted", "teal"), unsafe_allow_html=True)
            w4.markdown(metric_card("Max Drawdown", _sf(wf.max_drawdown, '.1%'), "worst peak-trough", "red"), unsafe_allow_html=True)

        # ── Recommended portfolio table ───────────────────────────────────
        st.markdown('<div class="section-title">Recommended Portfolio · regime-optimised allocation</div>',
                    unsafe_allow_html=True)
        alloc_rows = []
        for a in result.allocations:
            price_str = f"${a.price:,.2f}" if a.price is not None else "—"
            alloc_rows.append({
                "Ticker": a.ticker,
                "Class": "📈 Equity" if a.asset_class=="equity" else "🏦 Fixed Income",
                "Weight": _sf(a.weight, '.1%'),
                "Allocation (ZAR)": rand(a.dollar_amount),
                "Price": price_str,
                "Units": _sf(a.shares, '.4f'),
                "Rationale": a.rationale,
            })
        st.dataframe(pd.DataFrame(alloc_rows), use_container_width=True, hide_index=True)

        cc1, cc2 = st.columns(2)
        cc1.markdown(metric_card("Total Invested", rand(result.total_invested),
                                 f"of {rand_raw(budget_zar)} budget", "gold"), unsafe_allow_html=True)
        cc2.markdown(metric_card("Expected Volatility", _sf(result.expected_volatility, '.1%'),
                                 "annualised", "blue"), unsafe_allow_html=True)

        # ── Undervalued picks + fundamentals ──────────────────────────────
        st.markdown('<div class="section-title">Undervalued Picks · fundamental justification</div>',
                    unsafe_allow_html=True)
        screened = bundle.get("equity", {}).get("screened_universe", pd.DataFrame())
        if not screened.empty:
            show_cols = {
                "ticker":"Ticker", "currentPrice":"Price ($)",
                "trailingPE":"P/E", "priceToBook":"P/B",
                "returnOnEquity":"ROE", "earningsYield":"Earn. Yield",
                "grahamMoS":"Graham MoS", "fcfYield":"FCF Yield",
                "compositeScore":"Value Score",
            }
            avail = [c for c in show_cols if c in screened.columns]
            disp = screened[avail].copy().rename(columns=show_cols)
            for pct_col in ["ROE","Earn. Yield","Graham MoS","FCF Yield"]:
                if pct_col in disp.columns:
                    disp[pct_col] = disp[pct_col].apply(
                        lambda v: f"{v:.1%}" if pd.notna(v) else "—")
            for num_col in ["P/E","P/B","Value Score"]:
                if num_col in disp.columns:
                    disp[num_col] = disp[num_col].apply(
                        lambda v: f"{v:.2f}" if pd.notna(v) else "—")
            if "Price ($)" in disp.columns:
                disp["Price ($)"] = disp["Price ($)"].apply(
                    lambda v: f"${v:,.2f}" if pd.notna(v) else "—")
            st.dataframe(disp, use_container_width=True, hide_index=True)
            st.caption("Screen gates: P/E < 20 · P/B < 3 · earnings yield > 5% · "
                       "ROE > 12% · D/E < 2×. Ranked by composite value score "
                       "(earnings yield + ROE + Graham margin-of-safety + FCF yield).")
        else:
            st.info("No stocks passed the value screen for this universe.")


# ──────────────────────────────────────────────────────────────────────────────
# TAB 2 — FOREX DESK
# ──────────────────────────────────────────────────────────────────────────────

with tab_fx:
    st.markdown('<div class="section-title">Live Signal Feed · session-timed entries with ATR risk control</div>',
                unsafe_allow_html=True)

    if not signals:
        st.info("No actionable forex signals right now. Signals require trend, "
                "momentum, session-timing and volatility filters to all align.")
    else:
        # Summary cards
        longs = sum(1 for s in signals if s.direction=="LONG")
        shorts = sum(1 for s in signals if s.direction=="SHORT")
        rec_active = sum(1 for s in signals if s.recovery_mode)
        f1,f2,f3,f4 = st.columns(4)
        f1.markdown(metric_card("Active Signals", str(len(signals)), "across major pairs", "teal"), unsafe_allow_html=True)
        f2.markdown(metric_card("Long / Short", f"{longs} / {shorts}", "directional bias", "blue"), unsafe_allow_html=True)
        f3.markdown(metric_card("Recovery Mode", str(rec_active), "trades recovering losses", "gold"), unsafe_allow_html=True)
        avg_conf = np.mean([s.confidence for s in signals]) if signals else 0
        f4.markdown(metric_card("Avg Confidence", f"{avg_conf:.0%}", "filter agreement", "green"), unsafe_allow_html=True)

        # ── Export signals to MetaTrader 5 ────────────────────────────────
        if _SIGNAL_EXPORT:
            ec1, ec2 = st.columns([3, 1])
            with ec2:
                if st.button("📡 Export to MT5", use_container_width=True,
                             help="Write atlas_signals.csv/.json for the MQL5 EA"):
                    try:
                        path = export_signals(signals, account_equity=budget_usd)
                        st.success(f"Exported → {path}")
                    except Exception as exc:
                        st.error(f"Export failed: {exc}")

        st.markdown("<br>", unsafe_allow_html=True)

        for s in sorted(signals, key=lambda x: x.confidence, reverse=True):
            dir_cls = "long" if s.direction=="LONG" else "short"
            badge_cls = "long" if s.direction=="LONG" else "short"
            entry_str = f"{s.entry_window_utc[0]:02d}:00–{s.entry_window_utc[1]:02d}:00 UTC"
            exit_str  = f"{s.exit_window_utc[0]:02d}:00–{s.exit_window_utc[1]:02d}:00 UTC"
            rec_badge = '<span class="badge rec">⟳ RECOVERY</span>' if s.recovery_mode else ''

            card = f"""
            <div class="sig-card {dir_cls}">
              <div class="sig-head">
                <span class="sig-pair">{s.pair}</span>
                <span>
                  <span class="badge {badge_cls}">{s.direction}</span>
                  {rec_badge}
                  <span style="color:#8b97ad;font-size:0.8rem;margin-left:8px;">
                  {s.regime} · {s.confidence:.0%} conf</span>
                </span>
              </div>
              <div class="sig-grid">
                <div class="sig-cell"><div class="k">⏱ Entry Window</div><div class="val v-teal">{entry_str}</div></div>
                <div class="sig-cell"><div class="k">🚪 Exit Window</div><div class="val v-blue">{exit_str}</div></div>
                <div class="sig-cell"><div class="k">Entry Price</div><div class="val">{s.entry_price}</div></div>
                <div class="sig-cell"><div class="k">Risk : Reward</div><div class="val v-gold">1 : {s.risk_reward:.2f}</div></div>
                <div class="sig-cell"><div class="k">🛑 Stop Loss</div><div class="val v-red">{s.stop_loss}</div></div>
                <div class="sig-cell"><div class="k">🎯 Take Profit</div><div class="val v-green">{s.take_profit}</div></div>
                <div class="sig-cell"><div class="k">Lot Size</div><div class="val">{s.lot_size}</div></div>
                <div class="sig-cell"><div class="k">$ Risk</div><div class="val">{rand(s.dollar_risk)}</div></div>
              </div>
            """
            if s.recovery_mode and s.recovery_deficit > 0:
                base_risk = budget_usd * 0.01
                mult = min(s.recovery_deficit / base_risk + 1, 3.0) if base_risk > 0 else 1.0
                fill_pct = min(mult / 3.0 * 100, 100)
                card += f"""
                <div class="rec-wrap">
                  <div style="display:flex;justify-content:space-between;font-size:0.82rem;">
                    <span style="color:#f0b90b;font-weight:700;">⟳ Recovery Sizing Active</span>
                    <span style="font-family:'JetBrains Mono';">×{mult:.2f} multiplier · deficit {rand(s.recovery_deficit)}</span>
                  </div>
                  <div class="rec-bar-bg"><div class="rec-bar-fill" style="width:{fill_pct:.0f}%;"></div></div>
                  <div style="color:#8b97ad;font-size:0.72rem;margin-top:6px;">
                    Next win sized to recover the running deficit + base profit · capped at ×3.0 (wipe-out guard)
                  </div>
                </div>
                """
            card += "</div>"
            st.markdown(card, unsafe_allow_html=True)

    # ── Recovery sizing explainer ─────────────────────────────────────────
    with st.expander("ℹ️  How Recovery Sizing works"):
        st.markdown("""
        The **RecoverySizer** always aims to *make back what was lost* while
        strictly preventing account wipe-out:

        1. **Base trade** risks 1% of equity on the stop-loss distance.
        2. After any stop-out, the dollar **deficit accumulates**.
        3. The **next trade is up-sized** so a single take-profit recovers the
           full deficit *plus* normal base profit:
           `recovery_lot = (deficit + base_risk) / (tp_distance × pip_value)`
        4. Lot size is **hard-capped at ×3.0** of base — controlled, not blind Martingale.
        5. A **circuit-breaker** resets the deficit if equity drawdown hits 15%,
           and no single trade may risk more than 3% of equity.
        """)

    # ── Forex walk-forward table ──────────────────────────────────────────
    if forex_wf:
        st.markdown('<div class="section-title">Walk-Forward Backtest · per-pair out-of-sample performance</div>',
                    unsafe_allow_html=True)
        wf_rows = []
        for pair, r in forex_wf.items():
            wf_rows.append({
                "Pair": r.pair, "Trades": r.total_trades,
                "Win Rate": _sf(r.win_rate, '.0%'),
                "Net PnL": rand(r.total_pnl_usd),
                "Profit Factor": _sf(r.profit_factor, '.2f'),
                "Max DD": rand(r.max_drawdown_usd),
                "Sharpe": _sf(r.sharpe, '.2f'),
            })
        if wf_rows:
            st.dataframe(pd.DataFrame(wf_rows), use_container_width=True, hide_index=True)


# ──────────────────────────────────────────────────────────────────────────────
# TAB 3 — FIXED INCOME
# ──────────────────────────────────────────────────────────────────────────────

with tab_fi:
    fi = bundle.get("fixed_income", {})
    st.markdown('<div class="section-title">Fixed-Income Allocation · yield to maturity</div>',
                unsafe_allow_html=True)

    etf_yields = fi.get("etf_yields", pd.DataFrame())
    if not etf_yields.empty and "ytm_proxy" in etf_yields.columns:
        valid = etf_yields.dropna(subset=["ytm_proxy"])
        if not valid.empty:
            # Top metric cards: best YTMs
            top = valid.sort_values("ytm_proxy", ascending=False).head(4)
            cols = st.columns(len(top))
            for col, (_, row) in zip(cols, top.iterrows()):
                col.markdown(metric_card(
                    row["ticker"],
                    f"{row['ytm_proxy']:.2%}",
                    f"{row.get('name','bond ETF')} · YTM", "gold"
                ), unsafe_allow_html=True)

        # YTM bar chart
        if _PLOTLY and not valid.empty:
            st.markdown("<br>", unsafe_allow_html=True)
            v = valid.sort_values("ytm_proxy")
            fig = go.Figure(go.Bar(
                x=v["ytm_proxy"]*100, y=v["ticker"], orientation="h",
                marker=dict(color=v["ytm_proxy"]*100,
                            colorscale=[[0,"#1ba8a0"],[1,"#f0b90b"]]),
                text=[f"{y:.2%}" for y in v["ytm_proxy"]],
                textposition="outside",
                hovertemplate="%{y}: %{x:.2f}%<extra></extra>",
            ))
            fig.update_layout(
                template="plotly_dark", height=300,
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=10,r=40,t=10,b=10),
                xaxis_title="Yield to Maturity (%)",
                font=dict(family="Inter", color="#e8edf7"),
            )
            fig.update_xaxes(gridcolor="rgba(255,255,255,0.05)")
            st.plotly_chart(fig, use_container_width=True)

        # Detail table
        st.markdown('<div class="section-title">Bond ETF Detail</div>', unsafe_allow_html=True)
        cols_map = {"ticker":"Ticker","name":"Fund","ytm_proxy":"YTM",
                    "ytm_source":"YTM Source","duration_years":"Duration (yrs)"}
        avail = [c for c in cols_map if c in etf_yields.columns]
        disp = etf_yields[avail].rename(columns=cols_map)
        if "YTM" in disp.columns:
            disp["YTM"] = disp["YTM"].apply(lambda v: f"{v:.2%}" if pd.notna(v) else "—")
        if "Duration (yrs)" in disp.columns:
            disp["Duration (yrs)"] = disp["Duration (yrs)"].apply(
                lambda v: f"{v:.1f}" if pd.notna(v) else "—")
        st.dataframe(disp, use_container_width=True, hide_index=True)
    else:
        st.info("Fixed-income YTM data unavailable for this run.")

    # ── Yield curve ───────────────────────────────────────────────────────
    curve = fi.get("yield_curve", pd.Series(dtype=float))
    slope = fi.get("curve_slope_bp")
    if curve is not None and not curve.empty:
        st.markdown('<div class="section-title">US Treasury Yield Curve</div>', unsafe_allow_html=True)
        cc1, cc2 = st.columns([2,1])
        if _PLOTLY:
            fig = go.Figure(go.Scatter(
                x=list(curve.index), y=[v*100 for v in curve.values],
                mode="lines+markers",
                line=dict(color="#2dd4bf", width=3),
                marker=dict(size=10, color="#f0b90b"),
                fill="tozeroy", fillcolor="rgba(45,212,191,0.08)",
            ))
            fig.update_layout(
                template="plotly_dark", height=300,
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=10,r=10,t=10,b=10),
                yaxis_title="Yield (%)", xaxis_title="Maturity",
                font=dict(family="Inter", color="#e8edf7"),
            )
            fig.update_yaxes(gridcolor="rgba(255,255,255,0.05)")
            cc1.plotly_chart(fig, use_container_width=True)

        if slope is not None:
            inverted = slope < 0
            cc2.markdown(metric_card(
                "10Y – 3M Spread", f"{slope:+.0f} bp",
                "⚠️ Inverted (recession signal)" if inverted else "Normal (upward sloping)",
                "red" if inverted else "green"
            ), unsafe_allow_html=True)
            cc2.markdown(metric_card(
                "Curve Read",
                "Defensive" if inverted else "Risk-on",
                "tilt fixed income longer" if inverted else "equities favoured",
                "gold" if inverted else "teal"
            ), unsafe_allow_html=True)


# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown(
    "<div style='text-align:center;color:#5a6478;font-size:0.75rem;margin-top:40px;'>"
    "Atlas Capital Desk · Educational tool, not financial advice · "
    "Data via yfinance · Execution layer: route signals to MQL5 (MT5) for live FX"
    "</div>",
    unsafe_allow_html=True,
)
