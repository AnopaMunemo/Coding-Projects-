"""
app.py — Atlas Capital | Institutional Portfolio & Forex Desk v3
════════════════════════════════════════════════════════════════════
Bloomberg Terminal × Apple × Goldman Sachs Marquee aesthetic.
All settings are in the ⚙️ Settings tab — no hidden sidebar.

Run: streamlit run app.py
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import streamlit as st

try:
    import plotly.graph_objects as go
    import plotly.express as px
    _PLOTLY = True
except ImportError:
    _PLOTLY = False

_ENGINE_ERROR: Optional[str] = None
try:
    from data_feed import (
        DataFeedConfig, EquityConfig, FixedIncomeConfig, ForexConfig,
        DataFeedOrchestrator,
    )
    from portfolio_optimizer import PortfolioRequest, PortfolioOptimizer, RISK_FREE_RATE_10Y
    from forex_engine import run_forex_engine
    _ENGINES = True
except Exception as exc:
    _ENGINES = False
    _ENGINE_ERROR = str(exc)
    RISK_FREE_RATE_10Y = 0.0445

try:
    from strategies import ALL_STRATEGIES, STRATEGY_CATEGORIES, run_all_strategies, aggregate_signal
    _STRATEGIES = True
except Exception:
    _STRATEGIES = False

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
# Page config — NO sidebar, full width
# ══════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Atlas Capital · Institutional Desk",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600&display=swap');

/* ── Root palette ── */
:root {
  --bg:      #070B14;
  --card:    #111827;
  --card2:   #162235;
  --border:  rgba(255,255,255,0.07);
  --accent:  #00D4FF;
  --success: #00E676;
  --warn:    #FFB020;
  --danger:  #FF5252;
  --purple:  #818CF8;
  --txt:     #FFFFFF;
  --txt2:    #94A3B8;
  --txt3:    #4B5563;
  --glow:    rgba(0,212,255,0.15);
}

/* ── Global ── */
html, body, .stApp {
  background: var(--bg) !important;
  color: var(--txt) !important;
  font-family: 'Inter', sans-serif !important;
}
.block-container { padding: 0 2rem 3rem 2rem !important; max-width: 1600px !important; }
#MainMenu, footer, header, section[data-testid="stSidebar"] { display: none !important; }

/* ── Top nav bar ── */
.atlas-nav {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 0 18px 0; border-bottom: 1px solid var(--border);
  margin-bottom: 24px;
}
.atlas-logo { font-size: 1.15rem; font-weight: 800; letter-spacing: -0.3px; color: var(--txt); }
.atlas-logo span { color: var(--accent); }
.atlas-badge {
  display: inline-flex; align-items: center; gap: 6px;
  background: rgba(0,230,118,0.10); border: 1px solid rgba(0,230,118,0.25);
  border-radius: 99px; padding: 4px 12px; font-size: 0.72rem; font-weight: 600; color: var(--success);
}
.atlas-badge::before { content: '●'; font-size: 0.6rem; }

/* ── Premium metric cards ── */
.kpi {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 22px 24px;
  transition: transform .2s, border-color .2s, box-shadow .2s;
  position: relative; overflow: hidden;
}
.kpi::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
  opacity: 0;
  transition: opacity .2s;
}
.kpi:hover { transform: translateY(-2px); border-color: rgba(0,212,255,0.3); box-shadow: 0 8px 32px rgba(0,212,255,0.08); }
.kpi:hover::before { opacity: 1; }
.kpi .k-label { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 1.2px; color: var(--txt2); font-weight: 600; margin-bottom: 8px; }
.kpi .k-value { font-size: 2rem; font-weight: 800; font-family: 'JetBrains Mono', monospace; line-height: 1; margin-bottom: 6px; }
.kpi .k-sub { font-size: 0.74rem; color: var(--txt2); }
.k-accent  { color: var(--accent); }
.k-success { color: var(--success); }
.k-warn    { color: var(--warn); }
.k-danger  { color: var(--danger); }
.k-purple  { color: var(--purple); }
.k-white   { color: var(--txt); }

/* ── Section headers ── */
.sec-head {
  font-size: 0.68rem; text-transform: uppercase; letter-spacing: 1.5px;
  color: var(--txt2); font-weight: 700; margin: 28px 0 14px 0;
  display: flex; align-items: center; gap: 10px;
}
.sec-head::after { content: ''; flex: 1; height: 1px; background: var(--border); }

/* ── Hero ── */
.hero-block {
  padding: 36px 0 28px 0;
}
.hero-block h1 {
  font-size: 3rem; font-weight: 900; letter-spacing: -1.5px; line-height: 1.05;
  margin: 0 0 10px 0;
  background: linear-gradient(135deg, #fff 30%, var(--accent) 70%, #818CF8);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.hero-block .hero-sub { font-size: 1rem; color: var(--txt2); max-width: 560px; line-height: 1.6; }

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
  gap: 2px; background: var(--card); border-radius: 12px; padding: 4px;
  border: 1px solid var(--border); margin-bottom: 24px;
}
.stTabs [data-baseweb="tab"] {
  background: transparent; border-radius: 9px; padding: 9px 20px;
  color: var(--txt2); font-weight: 600; font-size: 0.85rem; border: none;
  transition: all .15s;
}
.stTabs [aria-selected="true"] {
  background: var(--card2) !important; color: var(--txt) !important;
  box-shadow: 0 1px 6px rgba(0,0,0,0.4);
}
.stTabs [data-baseweb="tab-panel"] { padding: 0 !important; }

/* ── Settings inputs ── */
.settings-card {
  background: var(--card); border: 1px solid var(--border); border-radius: 16px;
  padding: 24px 26px; margin-bottom: 16px;
}
.settings-card h3 { font-size: 0.90rem; font-weight: 700; color: var(--txt); margin: 0 0 16px 0; }

/* ── Data table ── */
.stDataFrame { border-radius: 12px !important; border: 1px solid var(--border) !important; }
.stDataFrame td, .stDataFrame th { font-size: 0.80rem !important; }

/* ── Buttons ── */
.stButton > button {
  background: var(--accent) !important; color: #000 !important;
  font-weight: 700 !important; border: none !important; border-radius: 10px !important;
  padding: 10px 20px !important; font-size: 0.88rem !important;
  transition: all .18s !important;
}
.stButton > button:hover { box-shadow: 0 0 20px rgba(0,212,255,0.4) !important; transform: translateY(-1px) !important; }
button[kind="secondary"] { background: var(--card2) !important; color: var(--txt) !important; }

/* ── Sliders ── */
.stSlider > div > div > div { background: var(--card2) !important; }
[data-testid="stSlider"] > div > div > div > div { background: var(--accent) !important; }

/* ── Select + number ── */
.stSelectbox > div, .stNumberInput > div { background: var(--card2) !important; border-radius: 8px !important; }
[data-baseweb="select"] { background: var(--card2) !important; }
[data-baseweb="input"] { background: var(--card2) !important; }

/* ── Signal cards ── */
.sig { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 18px 20px; margin-bottom: 12px; }
.sig.sig-long  { border-left: 3px solid var(--success); }
.sig.sig-short { border-left: 3px solid var(--danger); }
.sig-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
.sig-pair { font-size: 1.1rem; font-weight: 800; font-family: 'JetBrains Mono', monospace; }
.sig-grid { display: grid; grid-template-columns: repeat(4,1fr); gap: 12px; }
.sig-cell .k { font-size: 0.65rem; color: var(--txt2); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 3px; }
.sig-cell .v { font-size: 0.92rem; font-weight: 700; font-family: 'JetBrains Mono', monospace; }
.tag { display: inline-block; padding: 2px 10px; border-radius: 99px; font-size: 0.68rem; font-weight: 700; letter-spacing: 0.5px; }
.tag-long  { background: rgba(0,230,118,0.12); color: var(--success); border: 1px solid rgba(0,230,118,0.3); }
.tag-short { background: rgba(255,82,82,0.12);  color: var(--danger);  border: 1px solid rgba(255,82,82,0.3); }
.tag-flat  { background: rgba(148,163,184,0.12); color: var(--txt2);   border: 1px solid rgba(148,163,184,0.3); }
.tag-rec   { background: rgba(255,176,32,0.12);  color: var(--warn);   border: 1px solid rgba(255,176,32,0.3); }

/* ── Recovery bar ── */
.rec-panel { background: rgba(255,176,32,0.06); border: 1px solid rgba(255,176,32,0.2); border-radius: 10px; padding: 12px 14px; margin-top: 12px; }
.rec-bar-bg { height: 6px; background: rgba(255,255,255,0.06); border-radius: 99px; overflow: hidden; margin-top: 8px; }
.rec-bar-fill { height: 100%; background: linear-gradient(90deg,var(--warn),var(--danger)); border-radius: 99px; }

/* ── Strategy cards ── */
.strat { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; margin-bottom: 8px; }
.strat.s-long  { border-left: 3px solid var(--success); }
.strat.s-short { border-left: 3px solid var(--danger); }
.strat.s-flat  { border-left: 3px solid var(--txt3); }
.str-bar { height: 4px; background: rgba(255,255,255,0.06); border-radius: 99px; overflow: hidden; margin-top: 6px; }
.str-long  { height: 100%; background: var(--success); border-radius: 99px; }
.str-short { height: 100%; background: var(--danger); border-radius: 99px; }
.str-flat  { height: 100%; background: var(--txt3); border-radius: 99px; }

/* ── Divider ── */
hr { border: none; border-top: 1px solid var(--border) !important; margin: 20px 0 !important; }

/* ── Alert overrides ── */
[data-testid="stAlert"] { background: var(--card2) !important; border-radius: 10px !important; border: 1px solid var(--border) !important; }

/* ── Landing hero ── */
.landing-hero { padding: 56px 0 40px 0; border-bottom: 1px solid var(--border); margin-bottom: 32px; }
.landing-hero h1 { font-size: 3.6rem; font-weight: 900; letter-spacing: -2px; line-height: 1.05; margin: 0 0 16px 0; background: linear-gradient(135deg,#fff 20%,var(--accent) 60%,#818CF8 90%); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }
.landing-hero .l-sub { font-size: 1.05rem; color: var(--txt2); max-width: 640px; line-height: 1.65; margin-bottom: 28px; }
.l-tags { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:20px; }
.l-pill { display:inline-block; background: rgba(0,212,255,0.08); border: 1px solid rgba(0,212,255,0.2); border-radius: 99px; padding: 4px 14px; font-size:0.74rem; font-weight:600; color:var(--accent); }
.l-stats { display:flex; gap:32px; flex-wrap:wrap; padding-top:16px; }
.l-stat-num { font-size:2rem; font-weight:800; font-family:'JetBrains Mono',monospace; color:var(--txt); }
.l-stat-lbl { font-size:0.72rem; color:var(--txt2); text-transform:uppercase; letter-spacing:0.8px; margin-top:2px; }

/* ── Scenario cards ── */
.scenario { background:var(--card); border:1px solid var(--border); border-radius:14px; padding:20px 22px; margin-bottom:12px; }
.scenario.s-crash { border-left:3px solid var(--danger); }
.scenario.s-shock { border-left:3px solid var(--warn); }
.scenario.s-bull  { border-left:3px solid var(--success); }
.scenario-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin-top:12px; }
.sc-cell .k { font-size:0.65rem; color:var(--txt2); text-transform:uppercase; letter-spacing:0.5px; margin-bottom:3px; }
.sc-cell .v { font-size:0.95rem; font-weight:700; font-family:'JetBrains Mono',monospace; }

/* ── Health score ring ── */
.health-ring { text-align:center; padding:10px; }
.health-score { font-size:3rem; font-weight:900; font-family:'JetBrains Mono',monospace; }

/* ── Factor row ── */
.factor-row { display:flex; align-items:center; gap:16px; padding:10px 0; border-bottom:1px solid var(--border); }
.factor-name { width:180px; font-size:0.82rem; font-weight:600; color:var(--txt); }
.factor-bar-bg { flex:1; height:6px; background:rgba(255,255,255,0.06); border-radius:99px; overflow:hidden; }
.factor-bar-fill { height:100%; border-radius:99px; }
.factor-val { width:80px; text-align:right; font-size:0.82rem; font-family:'JetBrains Mono',monospace; color:var(--txt2); }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _sf(v, fmt, fallback="—"):
    if v is None:
        return fallback
    try:
        if isinstance(v, float) and math.isnan(v):
            return fallback
        return format(v, fmt)
    except Exception:
        return fallback


@st.cache_data(show_spinner=False, ttl=3600)
def _get_usd_zar() -> float:
    try:
        import yfinance as yf
        df = yf.download("ZAR=X", period="5d", interval="1d", progress=False, auto_adjust=True)
        if not df.empty:
            v = float(df["Close"].dropna().iloc[-1])
            if 5 < v < 40:
                return v
    except Exception:
        pass
    return 18.5


def _rand(amount) -> str:
    if amount is None:
        return "R—"
    try:
        return f"R{float(amount) * st.session_state.get('usd_zar', 18.5):,.2f}"
    except Exception:
        return "R—"


def _rand_raw(v) -> str:
    if v is None:
        return "R—"
    try:
        return f"R{float(v):,.2f}"
    except Exception:
        return "R—"


def kpi(label, value, sub="", color="white"):
    return f"""<div class="kpi">
        <div class="k-label">{label}</div>
        <div class="k-value k-{color}">{value}</div>
        <div class="k-sub">{sub}</div>
    </div>"""


# ══════════════════════════════════════════════════════════════════════════════
# Session-state defaults (all settings live here — no sidebar)
# ══════════════════════════════════════════════════════════════════════════════

DEFAULTS = {
    "budget_zar":    300.0,
    "risk_appetite": "Moderate",
    "time_horizon":  8,
    "target_return": 20,       # integer %
    "stock_type":    "Value",
    "use_live":      True,
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

usd_zar = _get_usd_zar()
st.session_state["usd_zar"] = usd_zar


# ══════════════════════════════════════════════════════════════════════════════
# Top navigation bar
# ══════════════════════════════════════════════════════════════════════════════

budget_zar    = float(st.session_state["budget_zar"])
budget_usd    = budget_zar / usd_zar
risk_appetite = st.session_state["risk_appetite"]
time_horizon  = int(st.session_state["time_horizon"])
target_return = float(st.session_state["target_return"]) / 100.0
stock_type    = st.session_state["stock_type"]
use_live      = st.session_state["use_live"]

n1, n2, n3 = st.columns([3, 2, 1])
n1.markdown(f"""
<div class="atlas-nav">
  <div>
    <span class="atlas-logo">🏛️ Atlas<span>Capital</span></span>
    <span style="color:#4B5563;font-size:0.78rem;margin-left:12px;">Institutional Portfolio & Forex Desk</span>
  </div>
</div>
""", unsafe_allow_html=True)
n2.markdown(f"""
<div style="padding-top:14px;display:flex;gap:12px;align-items:center;">
  <span class="atlas-badge">LIVE {datetime.now():%H:%M}</span>
  <span style="font-size:0.78rem;color:#94A3B8;">USD/ZAR {usd_zar:.2f}</span>
  <span style="font-size:0.78rem;color:#94A3B8;">TB Rate {RISK_FREE_RATE_10Y:.2%}</span>
</div>
""", unsafe_allow_html=True)
n3.markdown(f"""
<div style="padding-top:14px;text-align:right;">
  <span style="font-size:1.1rem;font-weight:800;color:#00D4FF;">{_rand_raw(budget_zar)}</span><br>
  <span style="font-size:0.72rem;color:#94A3B8;">{risk_appetite} · {time_horizon}m horizon</span>
</div>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Data loaders
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(show_spinner=False, ttl=1800)
def _load_bundle(theme: str) -> Dict[str, Any]:
    if not _ENGINES:
        return {}
    universes = {
        "Tech":     ["AAPL","MSFT","NVDA","GOOGL","META","AMD","AVGO","ORCL","CRM","TSM"],
        "Value":    ["BRK-B","JPM","XOM","CVX","JNJ","KO","PG","BAC","WFC","GS"],
        "Dividend": ["KO","PEP","JNJ","PG","MCD","MMM","T","VZ","O","PM"],
        "Emerging": ["EEM","VWO","INDA","EWZ","MCHI","EWY","EWT","EWJ","GXC","KWEB"],
        "Balanced": ["AAPL","MSFT","JPM","JNJ","KO","XOM","V","GOOGL","PG","HD"],
    }.get(theme, ["AAPL","MSFT","JPM","JNJ","KO","XOM","V","GOOGL"])
    cfg = DataFeedConfig(
        equity=EquityConfig(tickers=universes, historical_period="2y"),
        fixed_income=FixedIncomeConfig(
            bond_etfs={"SHY":"1-3Y Treasury","IEF":"7-10Y Treasury",
                       "TLT":"20+Y Treasury","AGG":"US Aggregate","LQD":"IG Corporate","HYG":"High Yield"},
            historical_period="1y",
        ),
        forex=ForexConfig(
            majors=["EURUSD=X","GBPUSD=X","USDJPY=X","AUDUSD=X","USDCAD=X"],
            crosses=["EURGBP=X","GBPJPY=X"], commodities=["XAUUSD=X"],
        ),
    )
    try:
        return DataFeedOrchestrator(cfg).run()
    except Exception as exc:
        logging.error("Pipeline: %s", exc)
        return {}


@st.cache_data(show_spinner=False, ttl=1800)
def _load_bench() -> Optional[pd.Series]:
    try:
        import yfinance as yf
        df = yf.download("^GSPC", period="2y", interval="1d", progress=False, auto_adjust=True)
        if not df.empty:
            s = df["Close"]
            return (s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s).dropna()
    except Exception:
        pass
    return None


def _synthetic(theme: str) -> Dict[str, Any]:
    dates = pd.date_range(end=datetime.now(), periods=504, freq="B")
    def hist(seed, drift=0.0007, vol=0.013, p0=120.0):
        r = np.random.default_rng(seed)
        p = p0 * np.cumprod(1 + r.normal(drift, vol, len(dates)))
        df = pd.DataFrame({"Open": p*0.999,"High": p*1.007,"Low": p*0.993,
                           "Close": p,"Volume": r.uniform(1e6, 5e6, len(dates))}, index=dates)
        df["Return"] = df["Close"].pct_change()
        return df
    tickers = {
        "Tech":["AAPL","MSFT","NVDA","GOOGL","META","AMD"],
        "Value":["BRK-B","JPM","XOM","JNJ","KO","PG"],
        "Dividend":["KO","PEP","JNJ","PG","MCD","VZ"],
        "Emerging":["EEM","VWO","INDA","EWZ","MCHI","EWY"],
        "Balanced":["AAPL","MSFT","JPM","JNJ","KO","XOM"],
    }.get(theme, ["AAPL","MSFT","JPM","JNJ","KO","XOM"])
    histories = {t: hist(i, 0.0007, 0.013, 80+12*i) for i, t in enumerate(tickers)}
    screened = pd.DataFrame({
        "ticker": tickers[:4],
        "currentPrice": [float(histories[t]["Close"].iloc[-1]) for t in tickers[:4]],
        "trailingPE": [12.3, 14.8, 9.5, 17.1], "priceToBook": [1.8, 2.4, 1.1, 2.9],
        "returnOnEquity": [0.21, 0.18, 0.25, 0.15], "earningsYield": [0.081, 0.068, 0.105, 0.058],
        "grahamMoS": [0.18, 0.12, 0.28, 0.09], "fcfYield": [0.06, 0.05, 0.08, 0.04],
        "compositeScore": [0.88, 0.81, 0.94, 0.72],
    })
    fi_t = ["SHY","IEF","TLT","AGG","LQD","HYG"]
    etf_hist = {t: hist(50+i, 0.0002, 0.004, 95+5*i) for i, t in enumerate(fi_t)}
    etf_yields = pd.DataFrame({
        "ticker": fi_t, "name": ["1-3Y Treasury","7-10Y Treasury","20+Y Treasury","US Aggregate","IG Corporate","High Yield"],
        "ytm_proxy": [0.0495, 0.0438, 0.0421, 0.0455, 0.0532, 0.0781],
        "ytm_source": ["sec_30d_yield"]*6, "duration_years": [1.9, 7.5, 17.2, 6.1, 8.4, 3.8],
    })
    fx_pairs = ["EURUSD=X","GBPUSD=X","USDJPY=X","AUDUSD=X","XAUUSD=X"]
    fx_daily = {}
    for i, t in enumerate(fx_pairs):
        base = 1.1 if "USD=X" in t and "JPY" not in t else (150 if "JPY" in t else 1950)
        r = np.random.default_rng(200+i)
        p = base * np.cumprod(1 + r.normal(0.0002, 0.006, len(dates)))
        d = pd.DataFrame({"Open": p,"High": p*1.004,"Low": p*0.996,"Close": p,"Volume": np.zeros(len(dates))}, index=dates)
        d["Return"] = d["Close"].pct_change()
        fx_daily[t] = d
    return {
        "equity": {"histories": histories, "screened_universe": screened},
        "fixed_income": {
            "yield_curve": pd.Series({"3M":0.0521,"5Y":0.0432,"10Y":0.0418,"30Y":0.0445}),
            "curve_slope_bp": -103.0, "etf_yields": etf_yields, "etf_histories": etf_hist,
        },
        "forex": {"daily": fx_daily},
        "meta": {"run_at": datetime.now(timezone.utc).isoformat(), "synthetic": True},
    }


# ══════════════════════════════════════════════════════════════════════════════
# Build engine
# ══════════════════════════════════════════════════════════════════════════════

def _build() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    with st.status("🏛️ Atlas Capital — computing portfolio…", expanded=True) as status:
        st.write("📡 Fetching market data…")
        if use_live and _ENGINES:
            bundle = _load_bundle(stock_type)
            if not bundle or not bundle.get("equity", {}).get("histories"):
                st.write("⚠️ Live feed empty → synthetic demo.")
                bundle = _synthetic(stock_type)
        else:
            bundle = _synthetic(stock_type)
        out["bundle"] = bundle

        st.write("🧮 Portfolio optimisation (Beta · Treynor · Monte Carlo)…")
        if _ENGINES:
            req = PortfolioRequest(
                budget_usd=budget_usd, risk_appetite=risk_appetite,
                time_horizon_months=time_horizon, preferred_stock_type=stock_type,
                target_return=target_return, risk_free_rate=RISK_FREE_RATE_10Y,
            )
            opt = PortfolioOptimizer(req, bundle)
            result = opt.build()
            months_axis = sorted({3, 6, time_horizon, 12, 18, 24})
            targets_axis = [0.05, 0.10, 0.15, 0.20, 0.30, 0.50]
            matrix = opt.probability_matrix(months_axis, targets_axis)
            out.update({"result": result, "prob_matrix": matrix})

        st.write("💱 Forex signals + recovery sizing…")
        if _ENGINES:
            signals, wf = run_forex_engine(bundle.get("forex", {}), account_equity=budget_usd, backtest=True)
            out.update({"signals": signals, "forex_wf": wf})

        st.write("📊 Benchmark data…")
        out["benchmark"] = _load_bench() if use_live else None

        status.update(label="✅ Portfolio ready", state="complete", expanded=False)
    return out


needs_build = (
    "engine_out" not in st.session_state
    or st.session_state.get("_stale", False)
    or st.session_state.get("_force_build", False)
)
if needs_build:
    st.session_state["_stale"] = False
    st.session_state["_force_build"] = False
    st.session_state.engine_out = _build()

data        = st.session_state.engine_out
bundle      = data.get("bundle", {})
result      = data.get("result")
prob_matrix = data.get("prob_matrix", pd.DataFrame())
signals     = data.get("signals", [])
forex_wf    = data.get("forex_wf", {})
benchmark   = data.get("benchmark")


# ══════════════════════════════════════════════════════════════════════════════
# Luxury landing hero — always visible above tabs
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("""
<div class="landing-hero">
  <div class="l-tags">
    <span class="l-pill">🏛️ Institutional Grade</span>
    <span class="l-pill">🤖 AI-Powered</span>
    <span class="l-pill">🇿🇦 ZAR-Native</span>
    <span class="l-pill">📐 Factor Models</span>
    <span class="l-pill">🎲 Monte Carlo Engine</span>
  </div>
  <h1>Institutional Intelligence.<br>Retail Accessibility.</h1>
  <p class="l-sub">The same analytical frameworks used by hedge funds, private equity, and sovereign wealth funds — regime-aware portfolio construction, VaR/CVaR risk attribution, 25+ signal strategies, and Monte Carlo probability forecasting. Starting from <b style="color:#00D4FF;">R300</b>.</p>
  <div class="l-stats">
    <div><div class="l-stat-num">25+</div><div class="l-stat-lbl">Trading Strategies</div></div>
    <div><div class="l-stat-num">10,000</div><div class="l-stat-lbl">Monte Carlo Paths</div></div>
    <div><div class="l-stat-num">R300</div><div class="l-stat-lbl">Minimum Capital</div></div>
    <div><div class="l-stat-num">4.45%</div><div class="l-stat-lbl">TB Rate Threshold</div></div>
    <div><div class="l-stat-num">95%</div><div class="l-stat-lbl">VaR Confidence</div></div>
  </div>
</div>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Advanced risk metrics helper
# ══════════════════════════════════════════════════════════════════════════════

def _compute_risk_metrics(res, bndl, bench) -> Dict[str, Any]:
    hist_map = bndl.get("equity", {}).get("histories", {})
    blended, ws = None, 0.0
    for a in res.allocations:
        if a.asset_class != "equity":
            continue
        h = hist_map.get(a.ticker)
        if h is None or h.empty:
            continue
        ret = h["Close"].pct_change().dropna()
        blended = ret * a.weight if blended is None else blended.add(ret * a.weight, fill_value=0)
        ws += a.weight
    out: Dict[str, Any] = {k: None for k in ["var_95","es_95","sortino","alpha","info_ratio","health_score","port_ret"]}
    if blended is None or ws == 0:
        return out
    pr = (blended / ws).dropna()
    if len(pr) < 30:
        return out
    out["port_ret"] = pr
    ann = float(pr.mean()) * 252
    var_d = float(np.percentile(pr, 5))
    out["var_95"] = var_d * np.sqrt(252)
    tail = pr[pr <= var_d]
    out["es_95"] = float(tail.mean()) * np.sqrt(252) if len(tail) > 0 else out["var_95"]
    dn = pr[pr < 0]
    dn_std = float(dn.std()) * np.sqrt(252) if len(dn) > 0 else 0.01
    out["sortino"] = (ann - RISK_FREE_RATE_10Y) / max(dn_std, 1e-9)
    if bench is not None and not bench.empty:
        br = bench.pct_change().dropna()
        aln = pd.concat([pr, br], axis=1).dropna()
        if len(aln) > 30:
            aln.columns = ["p","b"]
            ann_b = float(aln["b"].mean()) * 252
            beta = float(np.cov(aln["p"], aln["b"])[0, 1] / max(np.var(aln["b"]), 1e-12))
            out["alpha"] = ann - RISK_FREE_RATE_10Y - beta * (ann_b - RISK_FREE_RATE_10Y)
            te = (aln["p"] - aln["b"]).std() * np.sqrt(252)
            out["info_ratio"] = (ann - ann_b) / te if te > 1e-9 else None
    sharpe = res.sharpe_ratio or 0
    dd_pen = abs(res.walk_forward.max_drawdown) if res.walk_forward and res.walk_forward.max_drawdown else 0.25
    hs = min(40, max(0, sharpe * 20))
    hs += min(30, max(0, (1 - dd_pen) * 30))
    hs += min(20, max(0, (out["sortino"] or 0) * 10))
    hs += 10 if res.regime == "Bull" else (5 if res.regime == "Sideways" else 0)
    out["health_score"] = min(100, int(hs))
    return out


# Scenario analysis data
SCENARIOS = {
    "2008 Financial Crisis": {"type":"crash","equity_shock":-0.52,"bond_bp":-200,"fx_shock":-0.25,"months":18,"desc":"GFC peak-to-trough drawdown"},
    "COVID-19 Crash (Mar 2020)": {"type":"crash","equity_shock":-0.34,"bond_bp":-100,"fx_shock":-0.20,"months":2,"desc":"Fastest -34% bear market in history"},
    "Rate Shock +200bp": {"type":"shock","equity_shock":-0.18,"bond_bp":+200,"fx_shock":+0.05,"months":6,"desc":"Aggressive central bank tightening"},
    "ZAR Crisis -30%": {"type":"shock","equity_shock":-0.10,"bond_bp":+150,"fx_shock":-0.30,"months":3,"desc":"Emerging market currency stress"},
    "2022 Bear Market": {"type":"shock","equity_shock":-0.25,"bond_bp":+300,"fx_shock":-0.10,"months":12,"desc":"Inflation + rate-hike double blow"},
    "Recovery Bull Run": {"type":"bull","equity_shock":+0.40,"bond_bp":-50,"fx_shock":+0.10,"months":12,"desc":"Post-recession risk-on recovery"},
}


# ══════════════════════════════════════════════════════════════════════════════
# Tab layout — Settings tab first so it's always visible
# ══════════════════════════════════════════════════════════════════════════════

tab_settings, tab_port, tab_fx, tab_strat, tab_fi, tab_risk = st.tabs([
    "  ⚙️  Settings  ",
    "  📊  Portfolio  ",
    "  💱  Forex Desk  ",
    "  🧠  Strategy Lab  ",
    "  🏦  Fixed Income  ",
    "  📐  Risk Engine  ",
])


# ──────────────────────────────────────────────────────────────────────────────
# TAB 0 — SETTINGS  (all controls here — never hidden)
# ──────────────────────────────────────────────────────────────────────────────

with tab_settings:
    st.markdown("""
    <div class="hero-block">
        <h1>Portfolio Builder</h1>
        <p class="hero-sub">Configure your investment parameters below, then click <b>Generate Strategy</b> to compute your personalised, regime-aware portfolio with Monte Carlo forecasting.</p>
    </div>
    """, unsafe_allow_html=True)

    sa, sb = st.columns([1, 1])

    with sa:
        st.markdown('<div class="settings-card">', unsafe_allow_html=True)
        st.markdown("### 💰 Investment Capital")

        new_budget = st.number_input(
            "Budget (ZAR)", min_value=50.0, max_value=10_000_000.0,
            value=float(st.session_state["budget_zar"]), step=50.0, format="%.2f",
            help="Your starting capital in South African Rand.",
        )
        st.session_state["budget_zar"] = new_budget

        st.markdown("**Quick select:**")
        qc = st.columns(4)
        for i, amt in enumerate([300, 1_000, 5_000, 25_000]):
            if qc[i].button(f"R{amt:,}", key=f"qs_{amt}"):
                st.session_state["budget_zar"] = float(amt)
                st.session_state["_stale"] = True
                st.rerun()

        usd_equiv = new_budget / usd_zar
        st.caption(f"≈ ${usd_equiv:,.2f} USD  ·  USD/ZAR {usd_zar:.4f}")

        st.markdown("---")
        st.markdown("### 🎯 Risk Appetite")
        risk_options = ["Conservative", "Moderate", "Aggressive"]
        risk_idx = risk_options.index(st.session_state.get("risk_appetite", "Moderate"))
        new_risk = st.select_slider(
            "Select your risk tolerance", options=risk_options, value=risk_options[risk_idx],
            help="Conservative = more bonds, Aggressive = more equities",
        )
        st.session_state["risk_appetite"] = new_risk
        risk_desc = {
            "Conservative": "🛡️ Capital preservation — up to 40% equity, heavy fixed income",
            "Moderate":      "⚖️  Balanced growth — 40–65% equity, moderate bonds",
            "Aggressive":    "🚀 Maximum growth — 70–95% equity, minimal fixed income",
        }
        st.info(risk_desc[new_risk])
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="settings-card">', unsafe_allow_html=True)
        st.markdown("### 📈 Asset Preference")
        type_options = ["Value", "Tech", "Dividend", "Emerging", "Balanced"]
        new_type = st.selectbox(
            "Preferred stock type", type_options,
            index=type_options.index(st.session_state.get("stock_type", "Value")),
        )
        st.session_state["stock_type"] = new_type
        type_desc = {
            "Value":    "P/E < 20 · P/B < 3 · Graham MoS · FCF yield screened",
            "Tech":     "AAPL MSFT NVDA GOOGL META AMD AVGO ORCL CRM TSM",
            "Dividend": "High-yield dividend payers: KO PEP JNJ PG MCD T VZ O PM",
            "Emerging": "Emerging market ETFs: EEM VWO INDA EWZ MCHI EWY EWT EWJ",
            "Balanced": "Multi-sector blue chips: AAPL MSFT JPM JNJ KO XOM V GOOGL PG HD",
        }
        st.caption(type_desc.get(new_type, ""))

        new_live = st.toggle(
            "🔴 Live market data (yfinance)",
            value=st.session_state.get("use_live", True),
            help="Off = instant synthetic demo, On = real market prices (20-60s)",
        )
        st.session_state["use_live"] = new_live
        if not new_live:
            st.caption("Synthetic mode: realistic demo data, no API calls needed.")
        st.markdown('</div>', unsafe_allow_html=True)

    with sb:
        st.markdown('<div class="settings-card">', unsafe_allow_html=True)
        st.markdown("### ⏳ Investment Horizon")
        new_horizon = st.slider(
            "How many months will you hold?",
            min_value=1, max_value=60,
            value=int(st.session_state.get("time_horizon", 8)),
            format="%d months",
            help="Longer horizons increase probability of hitting your target return.",
        )
        st.session_state["time_horizon"] = new_horizon
        horizon_label = "Short-term" if new_horizon <= 6 else ("Medium-term" if new_horizon <= 24 else "Long-term")
        st.caption(f"{horizon_label} horizon · Monte Carlo will simulate {new_horizon}-month paths")
        st.markdown("---")
        st.markdown("### 🎁 Target Profit")
        new_target = st.slider(
            "Target return you want to achieve (%)",
            min_value=5, max_value=100, step=5,
            value=int(st.session_state.get("target_return", 20)),
            format="%d%%",
            help="The engine will report your probability of hitting this.",
        )
        st.session_state["target_return"] = new_target
        st.caption(f"Monte Carlo will compute: P(gain ≥ {new_target}% over {new_horizon} months)")
        st.markdown("---")

        # Live summary of settings
        st.markdown("### 📋 Current Settings")
        bz = float(st.session_state["budget_zar"])
        st.markdown(f"""
        | Parameter | Value |
        |-----------|-------|
        | **Budget** | {_rand_raw(bz)} ≈ ${bz/usd_zar:,.2f} USD |
        | **Risk Appetite** | {st.session_state['risk_appetite']} |
        | **Horizon** | {st.session_state['time_horizon']} months |
        | **Target Return** | {st.session_state['target_return']}% |
        | **Asset Type** | {st.session_state['stock_type']} |
        | **TB Rate Filter** | ≥ {RISK_FREE_RATE_10Y:.2%} p.a. |
        """)
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("---")
    gc1, gc2, gc3 = st.columns([1, 2, 1])
    with gc2:
        if st.button("⚡ Generate Strategy", use_container_width=True):
            st.session_state["_force_build"] = True
            # update all live values before rebuild
            st.session_state["budget_zar"]    = new_budget
            st.session_state["risk_appetite"] = new_risk
            st.session_state["time_horizon"]  = new_horizon
            st.session_state["target_return"] = new_target
            st.session_state["stock_type"]    = new_type
            st.session_state["use_live"]      = new_live
            st.rerun()
        st.caption("⬆️ Adjust settings above and click Generate to recompute your portfolio.")

    if bundle.get("meta", {}).get("synthetic"):
        st.info("📊 Currently showing **synthetic demo data**. Toggle Live market data ON above and click Generate for real prices.")
    if not _ENGINES:
        st.error(f"Engine modules not found: {_ENGINE_ERROR}")


# ──────────────────────────────────────────────────────────────────────────────
# TAB 1 — PORTFOLIO
# ──────────────────────────────────────────────────────────────────────────────

with tab_port:
    if result is None:
        st.markdown("""
        <div style="text-align:center;padding:60px 20px;">
            <div style="font-size:3rem;margin-bottom:16px;">📊</div>
            <h2 style="color:#94A3B8;font-weight:600;">No portfolio generated yet</h2>
            <p style="color:#4B5563;">Go to the <b>⚙️ Settings</b> tab and click <b>⚡ Generate Strategy</b></p>
        </div>
        """, unsafe_allow_html=True)
    else:
        # ── PDF export ────────────────────────────────────────────────────
        xc1, xc2 = st.columns([5, 1])
        with xc2:
            if _REPORT:
                try:
                    pdf_bytes = build_pdf_report(
                        result=result, signals=signals,
                        fi_bundle=bundle.get("fixed_income", {}),
                        budget_zar=float(st.session_state["budget_zar"]),
                        usd_zar=usd_zar, forex_wf=forex_wf,
                    )
                    st.download_button(
                        "📄 Download PDF", data=pdf_bytes,
                        file_name=f"atlas_{datetime.now():%Y%m%d_%H%M}.pdf",
                        mime="application/pdf", use_container_width=True,
                    )
                except Exception as exc:
                    st.caption(f"PDF error: {exc}")
            else:
                st.caption("Install reportlab")

        # ── Hero ──────────────────────────────────────────────────────────
        regime_color = {"Bull": "success","Bear": "danger","Sideways": "warn"}.get(result.regime, "white")
        st.markdown(f"""
        <div class="hero-block">
            <h1>Portfolio Intelligence</h1>
            <p class="hero-sub">
                AI-driven portfolio construction using Monte Carlo simulation, regime detection,
                factor models and Treasury optimisation. Budget: <b style="color:#00D4FF;">{_rand_raw(float(st.session_state['budget_zar']))}</b>
                · {result.regime} regime · {stock_type} universe
            </p>
        </div>
        """, unsafe_allow_html=True)

        # ── Top KPI row ───────────────────────────────────────────────────
        k1, k2, k3, k4, k5 = st.columns(5)
        _prob = result.monte_carlo_prob
        pcolor = "success" if (_prob and _prob >= 0.6) else ("warn" if (_prob and _prob >= 0.4) else "danger")
        k1.markdown(kpi("Expected Return", _sf(result.expected_return, '.1%'), "annualised p.a.", "accent"), unsafe_allow_html=True)
        k2.markdown(kpi("Sharpe Ratio", _sf(result.sharpe_ratio, '.2f'), "risk-adjusted return", "success" if (result.sharpe_ratio or 0) > 1 else "warn"), unsafe_allow_html=True)
        k3.markdown(kpi(f"P(≥{int(target_return*100)}% gain)", _sf(_prob, '.0%'), f"over {time_horizon} months", pcolor), unsafe_allow_html=True)
        k4.markdown(kpi("Market Regime", result.regime or "—", f"{_sf(result.regime_confidence, '.0%')} confidence", regime_color), unsafe_allow_html=True)
        k5.markdown(kpi("Expected Volatility", _sf(result.expected_volatility, '.1%'), "annualised", "warn"), unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

        # ── Second KPI row ────────────────────────────────────────────────
        k6, k7, k8, k9 = st.columns(4)
        k6.markdown(kpi("Total Invested", _rand(result.total_invested), f"of {_rand_raw(float(st.session_state['budget_zar']))} budget", "white"), unsafe_allow_html=True)
        k7.markdown(kpi("Equity / FI Split", f"{_sf(result.equity_weight,'.0%')} / {_sf(result.fi_weight,'.0%')}", "equity / fixed income", "accent"), unsafe_allow_html=True)
        avg_beta = float(np.mean([a.beta for a in result.allocations if a.beta is not None])) if result.allocations else 1.0
        avg_tr   = float(np.mean([a.treynor_ratio for a in result.allocations if a.treynor_ratio is not None])) if result.allocations else 0.0
        k8.markdown(kpi("Avg Portfolio Beta", _sf(avg_beta, '.2f'), "systematic market risk", "purple"), unsafe_allow_html=True)
        k9.markdown(kpi("Avg Treynor Ratio", _sf(avg_tr, '.3f'), "(E[R]−Rf) / Beta", "accent"), unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

        # ── Monte Carlo banner ────────────────────────────────────────────
        st.markdown(f"""
        <div style="background:var(--card);border:1px solid var(--border);border-radius:16px;padding:24px 28px;margin-bottom:24px;">
            <div style="font-size:0.68rem;text-transform:uppercase;letter-spacing:1.2px;color:#94A3B8;font-weight:600;margin-bottom:10px;">Monte Carlo Probability Engine · {result.request.monte_carlo_sims:,} paths</div>
            <div style="font-size:1.6rem;font-weight:800;color:#fff;margin-bottom:8px;">
                Hold <span style="color:#00D4FF;">{time_horizon} months</span> →
                <span style="color:{'#00E676' if (_prob or 0) >= 0.5 else '#FFB020'};">{_sf(_prob, '.0%')}</span>
                likelihood of a <span style="color:#00D4FF;">{target_return:.0%}+</span> gain
            </div>
            <div style="color:#94A3B8;font-size:0.88rem;">
                Median: <b style="color:#00E676;">{_sf(result.mc_median_return, '+.1%')}</b> &nbsp;·&nbsp;
                Downside P10: <b style="color:#FF5252;">{_sf(result.mc_p10, '+.1%')}</b> &nbsp;·&nbsp;
                Upside P90: <b style="color:#00D4FF;">{_sf(result.mc_p90, '+.1%')}</b>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # ── Probability matrix heatmap ────────────────────────────────────
        st.markdown('<div class="sec-head">Probability Matrix</div>', unsafe_allow_html=True)
        if _PLOTLY and not prob_matrix.empty:
            z = prob_matrix.values * 100
            fig_pm = go.Figure(go.Heatmap(
                z=z, x=[f"{m}m" for m in prob_matrix.columns], y=list(prob_matrix.index),
                colorscale=[[0,"#111827"],[0.35,"#1a3a4a"],[0.65,"#0a6a7f"],[1,"#00D4FF"]],
                text=[[f"{v:.0f}%" for v in row] for row in z],
                texttemplate="%{text}", textfont={"size": 12, "family": "JetBrains Mono"},
                colorbar=dict(title="Prob%", tickfont=dict(color="#94A3B8")),
                hovertemplate="Hold %{x} · target %{y}<br>Probability: %{z:.0f}%<extra></extra>",
            ))
            fig_pm.update_layout(
                template="plotly_dark", height=320, paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)", margin=dict(l=10,r=10,t=10,b=10),
                xaxis_title="Holding period", yaxis_title="Target return",
                font=dict(family="Inter", color="#94A3B8"),
            )
            st.plotly_chart(fig_pm, use_container_width=True)

        # ── Equity curve ──────────────────────────────────────────────────
        st.markdown('<div class="sec-head">Simulated Equity Curve vs S&P 500</div>', unsafe_allow_html=True)
        if _PLOTLY:
            hist_map = bundle.get("equity", {}).get("histories", {})
            blended = None
            ws = 0.0
            for a in result.allocations:
                if a.asset_class != "equity":
                    continue
                h = hist_map.get(a.ticker)
                if h is None or h.empty:
                    continue
                ret = h["Close"].pct_change().fillna(0)
                blended = ret * a.weight if blended is None else blended.add(ret * a.weight, fill_value=0)
                ws += a.weight
            fig_eq = go.Figure()
            if blended is not None and ws > 0:
                pc = (1 + blended / ws).cumprod()
                pv = pc / pc.iloc[0] * float(st.session_state["budget_zar"])
                fig_eq.add_trace(go.Scatter(x=pv.index, y=pv.values, name="Atlas Strategy",
                    line=dict(color="#00D4FF", width=2.5), fill="tozeroy", fillcolor="rgba(0,212,255,0.06)"))
            if benchmark is not None and not benchmark.empty:
                b = benchmark / benchmark.iloc[0] * float(st.session_state["budget_zar"])
                fig_eq.add_trace(go.Scatter(x=b.index, y=b.values, name="S&P 500",
                    line=dict(color="#FFB020", width=1.8, dash="dot")))
            elif blended is not None:
                rng2 = np.random.default_rng(1)
                synth = pd.Series(float(st.session_state["budget_zar"]) * np.cumprod(1 + rng2.normal(0.0003, 0.010, len(blended))), index=blended.index)
                fig_eq.add_trace(go.Scatter(x=synth.index, y=synth.values, name="S&P 500 (proxy)", line=dict(color="#FFB020", width=1.5, dash="dot")))
            fig_eq.update_layout(
                template="plotly_dark", height=360, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0,r=0,t=10,b=0),
                legend=dict(orientation="h", y=1.05, x=0, bgcolor="rgba(0,0,0,0)"),
                yaxis_title="Value (ZAR)", hovermode="x unified",
                font=dict(family="Inter", color="#94A3B8"),
            )
            fig_eq.update_xaxes(gridcolor="rgba(255,255,255,0.04)")
            fig_eq.update_yaxes(gridcolor="rgba(255,255,255,0.04)")
            st.plotly_chart(fig_eq, use_container_width=True)

        # ── Walk-forward ──────────────────────────────────────────────────
        if result.walk_forward and result.walk_forward.windows > 0:
            wf = result.walk_forward
            st.markdown('<div class="sec-head">Walk-Forward Validation</div>', unsafe_allow_html=True)
            w1, w2, w3, w4 = st.columns(4)
            w1.markdown(kpi("WF Windows", str(wf.windows), "out-of-sample periods", "white"), unsafe_allow_html=True)
            w2.markdown(kpi("OOS Win Rate", _sf(wf.win_rate, '.0%'), "positive periods", "success"), unsafe_allow_html=True)
            w3.markdown(kpi("OOS Sharpe", _sf(wf.mean_oos_sharpe, '.2f'), "risk-adjusted", "accent"), unsafe_allow_html=True)
            w4.markdown(kpi("Max Drawdown", _sf(wf.max_drawdown, '.1%'), "peak-to-trough", "danger"), unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)

        # ── Advanced risk metrics ─────────────────────────────────────────
        rm = _compute_risk_metrics(result, bundle, benchmark)
        st.markdown('<div class="sec-head">Advanced Risk Analytics · VaR · Sortino · Alpha · Information Ratio</div>', unsafe_allow_html=True)
        ra1, ra2, ra3, ra4, ra5, ra6 = st.columns(6)
        hs = rm.get("health_score")
        hs_color = "success" if (hs or 0) >= 70 else ("warn" if (hs or 0) >= 45 else "danger")
        ra1.markdown(kpi("VaR 95% (ann.)", _sf(rm.get("var_95"), '+.1%'), "1-yr Value at Risk", "danger"), unsafe_allow_html=True)
        ra2.markdown(kpi("Exp. Shortfall", _sf(rm.get("es_95"), '+.1%'), "CVaR beyond VaR", "danger"), unsafe_allow_html=True)
        ra3.markdown(kpi("Sortino Ratio", _sf(rm.get("sortino"), '.2f'), "downside-adj. return", "accent"), unsafe_allow_html=True)
        ra4.markdown(kpi("Alpha (Jensen)", _sf(rm.get("alpha"), '+.1%'), "excess vs CAPM", "success" if (rm.get("alpha") or 0) > 0 else "warn"), unsafe_allow_html=True)
        ra5.markdown(kpi("Info Ratio", _sf(rm.get("info_ratio"), '.2f'), "active return / TE", "accent"), unsafe_allow_html=True)
        ra6.markdown(kpi("Health Score", f"{hs or '—'}/100", "composite risk grade", hs_color), unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

        # ── Asset allocation donut + Efficient Frontier ───────────────────
        if _PLOTLY and result.allocations:
            st.markdown('<div class="sec-head">Asset Allocation · Efficient Frontier</div>', unsafe_allow_html=True)
            dc1, dc2 = st.columns([1, 2])

            # Donut
            with dc1:
                labels = [a.ticker for a in result.allocations]
                vals   = [a.weight for a in result.allocations]
                colors_donut = ["#00D4FF","#00E676","#FFB020","#818CF8","#FF5252","#38BDF8","#34D399","#FBBF24","#A78BFA","#FB7185"]
                fig_donut = go.Figure(go.Pie(
                    labels=labels, values=vals, hole=0.6,
                    marker=dict(colors=colors_donut[:len(labels)], line=dict(color="#070B14", width=2)),
                    textfont=dict(family="JetBrains Mono", size=11),
                    hovertemplate="%{label}: %{percent}<extra></extra>",
                ))
                fig_donut.update_layout(
                    template="plotly_dark", height=280, paper_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=0,r=0,t=10,b=0),
                    showlegend=True, legend=dict(font=dict(size=10, color="#94A3B8"), bgcolor="rgba(0,0,0,0)"),
                    annotations=[dict(text="Allocation", x=0.5, y=0.5, font_size=13, font_color="#94A3B8", showarrow=False)],
                )
                st.plotly_chart(fig_donut, use_container_width=True)

            # Efficient Frontier
            with dc2:
                hist_map_ef = bundle.get("equity", {}).get("histories", {})
                ef_rets = {}
                for a in result.allocations:
                    if a.asset_class != "equity":
                        continue
                    h = hist_map_ef.get(a.ticker)
                    if h is not None and not h.empty and len(h) > 60:
                        ef_rets[a.ticker] = h["Close"].pct_change().dropna()
                if len(ef_rets) >= 2:
                    ef_df = pd.DataFrame(ef_rets).dropna()
                    mu_ef = ef_df.mean().values * 252
                    sig_ef = ef_df.cov().values * 252
                    n_ef = len(mu_ef)
                    rng_ef = np.random.default_rng(42)
                    N_ef = 2500
                    ws_ef = rng_ef.dirichlet(np.ones(n_ef), N_ef)
                    pr_ef = ws_ef @ mu_ef
                    pv_ef = np.sqrt(np.clip(np.einsum("ij,jk,ik->i", ws_ef, sig_ef, ws_ef), 0, None))
                    sr_ef = (pr_ef - RISK_FREE_RATE_10Y) / np.maximum(pv_ef, 1e-9)
                    fig_ef = go.Figure()
                    fig_ef.add_trace(go.Scatter(
                        x=pv_ef*100, y=pr_ef*100, mode="markers",
                        marker=dict(color=sr_ef, colorscale=[[0,"#FF5252"],[0.5,"#FFB020"],[1,"#00E676"]],
                                    size=3, opacity=0.5,
                                    colorbar=dict(title="Sharpe", thickness=10, tickfont=dict(color="#94A3B8", size=9))),
                        hovertemplate="Vol: %{x:.1f}%<br>Ret: %{y:.1f}%<extra></extra>", name="Portfolios",
                    ))
                    # Current portfolio point
                    cur_w = np.array([a.weight for a in result.allocations if a.asset_class == "equity" and a.ticker in ef_rets])
                    if len(cur_w) == n_ef and cur_w.sum() > 0:
                        cw = cur_w / cur_w.sum()
                        cv = float(np.sqrt(np.clip(cw @ sig_ef @ cw, 0, None))) * 100
                        cr = float(cw @ mu_ef) * 100
                        fig_ef.add_trace(go.Scatter(
                            x=[cv], y=[cr], mode="markers+text",
                            marker=dict(color="#00D4FF", size=14, symbol="star"),
                            text=["Your Portfolio"], textposition="top right",
                            textfont=dict(color="#00D4FF", size=11), name="Current",
                        ))
                    fig_ef.update_layout(
                        template="plotly_dark", height=280, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        margin=dict(l=0,r=0,t=10,b=0),
                        xaxis_title="Volatility (%)", yaxis_title="Return (%)",
                        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color="#94A3B8")),
                        font=dict(family="Inter", color="#94A3B8"),
                    )
                    fig_ef.update_xaxes(gridcolor="rgba(255,255,255,0.04)")
                    fig_ef.update_yaxes(gridcolor="rgba(255,255,255,0.04)")
                    st.plotly_chart(fig_ef, use_container_width=True)
                else:
                    st.caption("Need ≥2 equity tickers for Efficient Frontier.")

        # ── Monte Carlo fan chart ─────────────────────────────────────────
        if _PLOTLY:
            st.markdown('<div class="sec-head">Monte Carlo Probability Cone · P10 / P25 / P50 / P75 / P90</div>', unsafe_allow_html=True)
            rng_fan = np.random.default_rng(777)
            n_paths_fan = 600
            n_steps_fan = max(time_horizon * 21, 21)
            mu_d = (result.expected_return or 0.12) / 252
            vol_d = (result.expected_volatility or 0.18) / np.sqrt(252)
            paths_arr = np.ones((n_paths_fan, n_steps_fan + 1)) * float(st.session_state["budget_zar"])
            for _t in range(1, n_steps_fan + 1):
                paths_arr[:, _t] = paths_arr[:, _t-1] * (1 + rng_fan.normal(mu_d, vol_d, n_paths_fan))
            x_fan = list(range(n_steps_fan + 1))
            fan_pcts = {p: [float(np.percentile(paths_arr[:, t], p)) for t in range(n_steps_fan + 1)] for p in [10,25,50,75,90]}
            fig_fan = go.Figure()
            fig_fan.add_trace(go.Scatter(
                x=x_fan + x_fan[::-1], y=fan_pcts[90] + fan_pcts[10][::-1],
                fill="toself", fillcolor="rgba(0,212,255,0.04)",
                line=dict(color="rgba(0,0,0,0)"), name="P10–P90", showlegend=True,
            ))
            fig_fan.add_trace(go.Scatter(
                x=x_fan + x_fan[::-1], y=fan_pcts[75] + fan_pcts[25][::-1],
                fill="toself", fillcolor="rgba(0,212,255,0.09)",
                line=dict(color="rgba(0,0,0,0)"), name="P25–P75", showlegend=True,
            ))
            for pct, col, dash in [(10,"#FF5252","dot"),(25,"#FFB020","dot"),(50,"#00E676","solid"),(75,"#00D4FF","dot"),(90,"#818CF8","dot")]:
                fig_fan.add_trace(go.Scatter(
                    x=x_fan, y=fan_pcts[pct],
                    line=dict(color=col, width=2.0 if pct==50 else 1.2, dash=dash),
                    name=f"P{pct}",
                ))
            fig_fan.add_hline(
                y=float(st.session_state["budget_zar"]),
                line=dict(color="#4B5563", width=1, dash="dash"),
                annotation_text="Initial Capital", annotation_font_color="#4B5563",
            )
            fig_fan.update_layout(
                template="plotly_dark", height=360, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0,r=0,t=10,b=0),
                xaxis_title="Trading Days", yaxis_title="Portfolio Value (ZAR)",
                legend=dict(orientation="h", y=1.05, x=0, bgcolor="rgba(0,0,0,0)"),
                font=dict(family="Inter", color="#94A3B8"),
            )
            fig_fan.update_xaxes(gridcolor="rgba(255,255,255,0.03)")
            fig_fan.update_yaxes(gridcolor="rgba(255,255,255,0.03)")
            st.plotly_chart(fig_fan, use_container_width=True)

        # ── Correlation matrix ────────────────────────────────────────────
        if _PLOTLY:
            hist_map_c = bundle.get("equity", {}).get("histories", {})
            corr_tickers = [a.ticker for a in result.allocations if a.asset_class == "equity" and a.ticker in hist_map_c]
            if len(corr_tickers) >= 2:
                st.markdown('<div class="sec-head">Correlation Matrix · Portfolio Assets</div>', unsafe_allow_html=True)
                corr_df = pd.DataFrame({t: hist_map_c[t]["Close"].pct_change().dropna() for t in corr_tickers}).dropna()
                corr_m = corr_df.corr()
                fig_corr = go.Figure(go.Heatmap(
                    z=corr_m.values, x=corr_tickers, y=corr_tickers,
                    colorscale=[[0,"#FF5252"],[0.5,"#111827"],[1,"#00D4FF"]],
                    zmin=-1, zmax=1,
                    text=[[f"{v:.2f}" for v in row] for row in corr_m.values],
                    texttemplate="%{text}", textfont={"size":10, "family":"JetBrains Mono"},
                    colorbar=dict(title="ρ", tickfont=dict(color="#94A3B8")),
                    hovertemplate="%{x} / %{y}<br>ρ = %{z:.3f}<extra></extra>",
                ))
                fig_corr.update_layout(
                    template="plotly_dark", height=340, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=0,r=0,t=10,b=0),
                    font=dict(family="Inter", color="#94A3B8"),
                )
                st.plotly_chart(fig_corr, use_container_width=True)

        # ── Allocation table ──────────────────────────────────────────────
        st.markdown('<div class="sec-head">Portfolio Allocation · Beta · Treynor Ratio · TB-Rate Screened</div>', unsafe_allow_html=True)
        rows = []
        for a in result.allocations:
            passed = a.asset_class != "equity" or (a.expected_return >= RISK_FREE_RATE_10Y)
            rows.append({
                "Ticker":          a.ticker,
                "Class":           "📈 Equity" if a.asset_class == "equity" else "🏦 Fixed Income",
                "Weight":          _sf(a.weight, '.1%'),
                "Allocation (ZAR)":_rand(a.dollar_amount),
                "Price":           f"${a.price:,.2f}" if a.price else "—",
                "Units":           _sf(a.shares, '.4f'),
                "Exp. Return":     _sf(a.expected_return, '.1%'),
                "Beta":            _sf(a.beta, '.2f'),
                "Treynor":         _sf(a.treynor_ratio, '.3f'),
                "TB Filter":       "✅" if passed else "❌",
                "Rationale":       a.rationale,
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        # ── Undervalued picks ─────────────────────────────────────────────
        screened = bundle.get("equity", {}).get("screened_universe", pd.DataFrame())
        if not screened.empty:
            st.markdown('<div class="sec-head">Value Screen · Graham · FCF · ROE</div>', unsafe_allow_html=True)
            show_cols = {"ticker":"Ticker","currentPrice":"Price","trailingPE":"P/E","priceToBook":"P/B",
                         "returnOnEquity":"ROE","earningsYield":"Earn Yield","grahamMoS":"Graham MoS",
                         "fcfYield":"FCF Yield","compositeScore":"Score"}
            avail = [c for c in show_cols if c in screened.columns]
            disp = screened[avail].copy().rename(columns=show_cols)
            for pc in ["ROE","Earn Yield","Graham MoS","FCF Yield"]:
                if pc in disp.columns:
                    disp[pc] = disp[pc].apply(lambda v: f"{v:.1%}" if pd.notna(v) else "—")
            for nc in ["P/E","P/B","Score"]:
                if nc in disp.columns:
                    disp[nc] = disp[nc].apply(lambda v: f"{v:.2f}" if pd.notna(v) else "—")
            if "Price" in disp.columns:
                disp["Price"] = disp["Price"].apply(lambda v: f"${v:,.2f}" if pd.notna(v) else "—")
            st.dataframe(disp, use_container_width=True, hide_index=True)


# ──────────────────────────────────────────────────────────────────────────────
# TAB 2 — FOREX DESK
# ──────────────────────────────────────────────────────────────────────────────

with tab_fx:
    st.markdown("""
    <div class="hero-block" style="padding-bottom:16px;">
        <h1>Forex Desk</h1>
        <p class="hero-sub">Session-timed ATR signals with recovery position sizing. Entry/exit windows in UTC.</p>
    </div>
    """, unsafe_allow_html=True)

    if not signals:
        st.markdown("""
        <div style="text-align:center;padding:48px;background:var(--card);border:1px solid var(--border);border-radius:16px;">
            <div style="font-size:2rem;margin-bottom:12px;">💱</div>
            <p style="color:#94A3B8;">No actionable signals right now. All trend, momentum, session-timing and volatility filters must align simultaneously.</p>
        </div>
        """, unsafe_allow_html=True)
    else:
        f1, f2, f3, f4 = st.columns(4)
        longs = sum(1 for s in signals if s.direction == "LONG")
        shorts = sum(1 for s in signals if s.direction == "SHORT")
        rec_n = sum(1 for s in signals if s.recovery_mode)
        avg_c = float(np.mean([s.confidence for s in signals]))
        f1.markdown(kpi("Active Signals", str(len(signals)), "major pairs", "accent"), unsafe_allow_html=True)
        f2.markdown(kpi("Long / Short", f"{longs} / {shorts}", "directional bias", "success"), unsafe_allow_html=True)
        f3.markdown(kpi("Recovery Mode", str(rec_n), "loss recovery trades", "warn"), unsafe_allow_html=True)
        f4.markdown(kpi("Avg Confidence", f"{avg_c:.0%}", "filter agreement", "accent"), unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

        if _SIGNAL_EXPORT:
            ec1, ec2, ec3 = st.columns([3, 1, 1])
            with ec2:
                if st.button("📡 Export to MT5", use_container_width=True):
                    try:
                        path = export_signals(signals, account_equity=budget_usd)
                        st.success(f"Exported → {path}")
                    except Exception as exc:
                        st.error(str(exc))

        for s in sorted(signals, key=lambda x: x.confidence, reverse=True):
            dc = "long" if s.direction == "LONG" else "short"
            entry_str = f"{s.entry_window_utc[0]:02d}:00–{s.entry_window_utc[1]:02d}:00 UTC"
            exit_str  = f"{s.exit_window_utc[0]:02d}:00–{s.exit_window_utc[1]:02d}:00 UTC"
            rec_html = '<span class="tag tag-rec" style="margin-left:6px;">⟳ RECOVERY</span>' if s.recovery_mode else ''
            st.markdown(f"""
            <div class="sig sig-{dc}">
              <div class="sig-head">
                <span class="sig-pair">{s.pair}</span>
                <span>
                  <span class="tag tag-{dc}">{s.direction}</span>
                  {rec_html}
                  <span style="color:#94A3B8;font-size:0.75rem;margin-left:8px;">{s.regime} · {s.confidence:.0%}</span>
                </span>
              </div>
              <div class="sig-grid">
                <div class="sig-cell"><div class="k">⏱ Entry</div><div class="v" style="color:#00D4FF;">{entry_str}</div></div>
                <div class="sig-cell"><div class="k">🚪 Exit</div><div class="v" style="color:#818CF8;">{exit_str}</div></div>
                <div class="sig-cell"><div class="k">Entry Price</div><div class="v">{s.entry_price}</div></div>
                <div class="sig-cell"><div class="k">Risk : Reward</div><div class="v" style="color:#FFB020;">1 : {_sf(s.risk_reward, '.2f')}</div></div>
                <div class="sig-cell"><div class="k">🛑 Stop Loss</div><div class="v" style="color:#FF5252;">{s.stop_loss}</div></div>
                <div class="sig-cell"><div class="k">🎯 Take Profit</div><div class="v" style="color:#00E676;">{s.take_profit}</div></div>
                <div class="sig-cell"><div class="k">Lot Size</div><div class="v">{s.lot_size}</div></div>
                <div class="sig-cell"><div class="k">Risk (ZAR)</div><div class="v">{_rand(s.dollar_risk)}</div></div>
              </div>
            """, unsafe_allow_html=True)
            if s.recovery_mode and s.recovery_deficit > 0:
                base_risk = budget_usd * 0.01
                mult = min(s.recovery_deficit / base_risk + 1, 3.0) if base_risk > 0 else 1.0
                fill = min(mult / 3.0 * 100, 100)
                st.markdown(f"""
                <div class="rec-panel">
                  <div style="display:flex;justify-content:space-between;font-size:0.82rem;">
                    <span style="color:#FFB020;font-weight:700;">⟳ Recovery Sizing Active</span>
                    <span style="font-family:'JetBrains Mono';">×{mult:.2f} multiplier · deficit {_rand(s.recovery_deficit)}</span>
                  </div>
                  <div class="rec-bar-bg"><div class="rec-bar-fill" style="width:{fill:.0f}%;"></div></div>
                  <div style="color:#94A3B8;font-size:0.70rem;margin-top:6px;">Hard-capped ×3.0 · 15% drawdown circuit-breaker</div>
                </div>""", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)

    if forex_wf:
        st.markdown('<div class="sec-head">Walk-Forward Backtest · per pair</div>', unsafe_allow_html=True)
        wf_rows = []
        for pair, r in forex_wf.items():
            wf_rows.append({
                "Pair": r.pair, "Trades": r.total_trades,
                "Win Rate": _sf(r.win_rate, '.0%'),
                "Net PnL": _rand(r.total_pnl_usd),
                "Profit Factor": _sf(r.profit_factor, '.2f'),
                "Max DD": _rand(r.max_drawdown_usd),
                "Sharpe": _sf(r.sharpe, '.2f'),
            })
        st.dataframe(pd.DataFrame(wf_rows), use_container_width=True, hide_index=True)


# ──────────────────────────────────────────────────────────────────────────────
# TAB 3 — STRATEGY LAB
# ──────────────────────────────────────────────────────────────────────────────

with tab_strat:
    st.markdown("""
    <div class="hero-block" style="padding-bottom:16px;">
        <h1>Strategy Lab</h1>
        <p class="hero-sub">25+ signal generators: Trend, Momentum, Mean Reversion, SMC (Order Blocks, FVG, BOS/ChoCh), BTMM, Turtle, Seasonal & Candlestick patterns.</p>
    </div>
    """, unsafe_allow_html=True)

    if not _STRATEGIES:
        st.error("strategies.py not found — ensure it is in the same folder as app.py.")
    else:
        hist_map = bundle.get("equity", {}).get("histories", {})
        fx_map   = bundle.get("forex", {}).get("daily", {})
        all_tickers = list(hist_map.keys()) + list(fx_map.keys())

        if not all_tickers:
            st.info("No price data. Generate the portfolio first (Settings tab).")
        else:
            sl1, sl2, sl3 = st.columns([2, 2, 2])
            sel_ticker = sl1.selectbox("Analyse ticker", all_tickers, key="strat_tk")
            sel_cats = sl2.multiselect("Categories", STRATEGY_CATEGORIES, default=STRATEGY_CATEGORIES)
            show_flat = sl3.checkbox("Show FLAT signals", value=False)

            df_s = hist_map.get(sel_ticker) or fx_map.get(sel_ticker, pd.DataFrame())

            if df_s.empty or len(df_s) < 15:
                st.warning("Insufficient data.")
            else:
                sel_names = [s.name for s in ALL_STRATEGIES if s.category in sel_cats]
                sigs = run_all_strategies(df_s, selected_names=sel_names)
                con = aggregate_signal(sigs)

                # Consensus KPIs
                sc1, sc2, sc3, sc4 = st.columns(4)
                cd = con["direction"]
                cc = "success" if cd == "LONG" else ("danger" if cd == "SHORT" else "white")
                sc1.markdown(kpi("Consensus Signal", cd, f"score {con['score']:+.2f}", cc), unsafe_allow_html=True)
                sc2.markdown(kpi("Confidence", f"{con['confidence']:.0%}", "agreement", "accent"), unsafe_allow_html=True)
                sc3.markdown(kpi("Long / Short", f"{con['long_count']} / {con['short_count']}", f"Flat: {con['flat_count']}", "white"), unsafe_allow_html=True)
                sc4.markdown(kpi("Score Balance", f"{con.get('long_score',0):.1f}L / {con.get('short_score',0):.1f}S", "weighted strength", "accent"), unsafe_allow_html=True)
                st.markdown("<br>", unsafe_allow_html=True)

                # Gauge
                if _PLOTLY:
                    gv = (con["score"] + 1) / 2 * 100
                    fig_g = go.Figure(go.Indicator(
                        mode="gauge+number",
                        value=gv,
                        gauge={
                            "axis": {"range": [0, 100], "tickcolor": "#94A3B8"},
                            "bar": {"color": "#00D4FF"},
                            "steps": [
                                {"range": [0, 35], "color": "rgba(255,82,82,0.2)"},
                                {"range": [35, 65], "color": "rgba(148,163,184,0.1)"},
                                {"range": [65, 100], "color": "rgba(0,230,118,0.2)"},
                            ],
                            "threshold": {"line": {"color": "#FFB020", "width": 3}, "value": gv},
                            "bgcolor": "rgba(0,0,0,0)",
                        },
                        number={"font": {"color": "#fff", "family": "JetBrains Mono"}, "suffix": "%"},
                        title={"text": f"Bullish Consensus · {sel_ticker}", "font": {"color": "#94A3B8"}},
                    ))
                    fig_g.update_layout(
                        template="plotly_dark", height=200, paper_bgcolor="rgba(0,0,0,0)",
                        margin=dict(l=30, r=30, t=40, b=5), font=dict(color="#94A3B8"),
                    )
                    st.plotly_chart(fig_g, use_container_width=True)

                # Signal cards grouped by category
                cat_map: Dict[str, List] = {}
                for sig in sigs:
                    if not show_flat and sig.direction == "FLAT":
                        continue
                    cat_map.setdefault(sig.category, []).append(sig)

                if not cat_map:
                    st.info("All signals are FLAT. Enable 'Show FLAT signals' to see them.")

                for cat, cat_sigs in cat_map.items():
                    st.markdown(f'<div class="sec-head">{cat}</div>', unsafe_allow_html=True)
                    cols = st.columns(min(3, len(cat_sigs)))
                    for idx, sig in enumerate(cat_sigs):
                        col = cols[idx % len(cols)]
                        dc = sig.direction.lower()
                        if dc not in ("long", "short"):
                            dc = "flat"
                        fill = int(sig.strength * 100)
                        col.markdown(f"""
                        <div class="strat s-{dc}">
                          <div style="font-size:0.70rem;color:#94A3B8;font-weight:600;margin-bottom:4px;">{sig.strategy}</div>
                          <span class="tag tag-{dc}">{sig.direction}</span>
                          <div class="str-bar"><div class="str-{dc}" style="width:{fill}%;"></div></div>
                          <div style="font-size:0.68rem;color:#4B5563;margin-top:5px;">{sig.detail[:90]}{"…" if len(sig.detail) > 90 else ""}</div>
                        </div>""", unsafe_allow_html=True)

                # 90-day candlestick chart
                if _PLOTLY:
                    st.markdown('<div class="sec-head">Price Chart · 90 sessions · EMA 20 / 50 / 200</div>', unsafe_allow_html=True)
                    cd90 = df_s.tail(90)
                    fig_c = go.Figure()
                    fig_c.add_trace(go.Candlestick(
                        x=cd90.index, open=cd90["Open"], high=cd90["High"],
                        low=cd90["Low"], close=cd90["Close"], name=sel_ticker,
                        increasing=dict(line=dict(color="#00E676"), fillcolor="rgba(0,230,118,0.25)"),
                        decreasing=dict(line=dict(color="#FF5252"), fillcolor="rgba(255,82,82,0.25)"),
                    ))
                    for p, c_ in [(20, "#00D4FF"), (50, "#FFB020"), (200, "#818CF8")]:
                        em = cd90["Close"].ewm(span=p).mean()
                        fig_c.add_trace(go.Scatter(x=cd90.index, y=em, name=f"EMA{p}",
                            line=dict(color=c_, width=1.2, dash="dot")))
                    fig_c.update_layout(
                        template="plotly_dark", height=420, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        margin=dict(l=0, r=0, t=10, b=0), xaxis_rangeslider_visible=False,
                        legend=dict(orientation="h", y=1.05, x=0, bgcolor="rgba(0,0,0,0)"),
                        font=dict(family="Inter", color="#94A3B8"),
                    )
                    fig_c.update_xaxes(gridcolor="rgba(255,255,255,0.03)")
                    fig_c.update_yaxes(gridcolor="rgba(255,255,255,0.03)")
                    st.plotly_chart(fig_c, use_container_width=True)


# ──────────────────────────────────────────────────────────────────────────────
# TAB 4 — FIXED INCOME
# ──────────────────────────────────────────────────────────────────────────────

with tab_fi:
    st.markdown("""
    <div class="hero-block" style="padding-bottom:16px;">
        <h1>Fixed Income</h1>
        <p class="hero-sub">Bond ETF yields, duration analysis, and US Treasury yield curve with recession signal detection.</p>
    </div>
    """, unsafe_allow_html=True)

    fi = bundle.get("fixed_income", {})
    etf_yields = fi.get("etf_yields", pd.DataFrame())

    if not etf_yields.empty and "ytm_proxy" in etf_yields.columns:
        valid = etf_yields.dropna(subset=["ytm_proxy"])
        if not valid.empty:
            top = valid.sort_values("ytm_proxy", ascending=False).head(4)
            cols = st.columns(len(top))
            for col, (_, row) in zip(cols, top.iterrows()):
                col.markdown(kpi(row["ticker"], f"{row['ytm_proxy']:.2%}", f"{row.get('name','Bond ETF')} · YTM", "accent"), unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)

        if _PLOTLY and not valid.empty:
            v = valid.sort_values("ytm_proxy")
            fig_b = go.Figure(go.Bar(
                x=v["ytm_proxy"]*100, y=v["ticker"], orientation="h",
                marker=dict(color=v["ytm_proxy"]*100, colorscale=[[0,"#162235"],[1,"#00D4FF"]]),
                text=[f"{y:.2%}" for y in v["ytm_proxy"]], textposition="outside",
                hovertemplate="%{y}: %{x:.2f}%<extra></extra>",
            ))
            fig_b.update_layout(
                template="plotly_dark", height=260, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=60, t=10, b=10), xaxis_title="Yield to Maturity (%)",
                font=dict(family="Inter", color="#94A3B8"),
            )
            fig_b.update_xaxes(gridcolor="rgba(255,255,255,0.04)")
            st.plotly_chart(fig_b, use_container_width=True)

        cm = {"ticker":"Ticker","name":"Fund","ytm_proxy":"YTM","ytm_source":"YTM Source","duration_years":"Duration (yrs)"}
        av = [c for c in cm if c in etf_yields.columns]
        dp = etf_yields[av].rename(columns=cm)
        if "YTM" in dp.columns:
            dp["YTM"] = dp["YTM"].apply(lambda v: f"{v:.2%}" if pd.notna(v) else "—")
        if "Duration (yrs)" in dp.columns:
            dp["Duration (yrs)"] = dp["Duration (yrs)"].apply(lambda v: f"{v:.1f}" if pd.notna(v) else "—")
        st.dataframe(dp, use_container_width=True, hide_index=True)
    else:
        st.info("Fixed-income data unavailable. Run with Live data enabled.")

    curve = fi.get("yield_curve", pd.Series(dtype=float))
    slope = fi.get("curve_slope_bp")
    if curve is not None and not curve.empty:
        st.markdown('<div class="sec-head">US Treasury Yield Curve</div>', unsafe_allow_html=True)
        c1, c2 = st.columns([2, 1])
        if _PLOTLY:
            fig_yc = go.Figure(go.Scatter(
                x=list(curve.index), y=[v*100 for v in curve.values], mode="lines+markers",
                line=dict(color="#00D4FF", width=2.5), marker=dict(size=9, color="#FFB020"),
                fill="tozeroy", fillcolor="rgba(0,212,255,0.06)",
            ))
            fig_yc.update_layout(
                template="plotly_dark", height=260, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0,r=0,t=10,b=10), yaxis_title="Yield (%)", xaxis_title="Maturity",
                font=dict(family="Inter", color="#94A3B8"),
            )
            fig_yc.update_yaxes(gridcolor="rgba(255,255,255,0.04)")
            c1.plotly_chart(fig_yc, use_container_width=True)
        if slope is not None:
            inv = slope < 0
            c2.markdown(kpi("10Y–3M Spread", f"{slope:+.0f} bp",
                            "⚠️ Inverted — recession signal" if inv else "Normal upward slope",
                            "danger" if inv else "success"), unsafe_allow_html=True)
            c2.markdown("<br>", unsafe_allow_html=True)
            c2.markdown(kpi("Signal", "Defensive" if inv else "Risk-on",
                            "tilt FI longer" if inv else "equities favoured",
                            "warn" if inv else "accent"), unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────────────────────
# TAB 5 — RISK ENGINE
# ──────────────────────────────────────────────────────────────────────────────

with tab_risk:
    st.markdown("""
    <div class="hero-block" style="padding-bottom:16px;">
        <h1>Risk Engine</h1>
        <p class="hero-sub">Scenario stress testing, factor attribution, and tail-risk analysis. Models inspired by Deloitte, PwC, KPMG, and EY risk frameworks.</p>
    </div>
    """, unsafe_allow_html=True)

    if result is None:
        st.markdown("""
        <div style="text-align:center;padding:60px 20px;">
            <div style="font-size:3rem;margin-bottom:16px;">📐</div>
            <h2 style="color:#94A3B8;font-weight:600;">Generate a portfolio first</h2>
            <p style="color:#4B5563;">Go to <b>⚙️ Settings</b> → <b>⚡ Generate Strategy</b></p>
        </div>""", unsafe_allow_html=True)
    else:
        rm2 = _compute_risk_metrics(result, bundle, benchmark)

        # ── Risk KPIs ─────────────────────────────────────────────────────
        rk1, rk2, rk3, rk4, rk5 = st.columns(5)
        hs2 = rm2.get("health_score")
        hs2_col = "success" if (hs2 or 0) >= 70 else ("warn" if (hs2 or 0) >= 45 else "danger")
        rk1.markdown(kpi("VaR 95% (ann.)", _sf(rm2.get("var_95"), '+.1%'), "max 1-yr loss at 95%", "danger"), unsafe_allow_html=True)
        rk2.markdown(kpi("CVaR / ES", _sf(rm2.get("es_95"), '+.1%'), "expected tail loss", "danger"), unsafe_allow_html=True)
        rk3.markdown(kpi("Sortino Ratio", _sf(rm2.get("sortino"), '.2f'), "downside risk-adj.", "accent"), unsafe_allow_html=True)
        rk4.markdown(kpi("Alpha (Jensen)", _sf(rm2.get("alpha"), '+.1%'), "excess vs CAPM", "success" if (rm2.get("alpha") or 0) > 0 else "warn"), unsafe_allow_html=True)
        rk5.markdown(kpi("Health Score", f"{hs2 or '—'}/100", "composite risk grade", hs2_col), unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

        # ── Health Score gauge ────────────────────────────────────────────
        if _PLOTLY and hs2 is not None:
            hg1, hg2 = st.columns([1, 3])
            with hg1:
                hs_color_hex = "#00E676" if hs2 >= 70 else ("#FFB020" if hs2 >= 45 else "#FF5252")
                fig_hs = go.Figure(go.Indicator(
                    mode="gauge+number",
                    value=hs2,
                    gauge={
                        "axis": {"range": [0, 100], "tickcolor": "#94A3B8", "tickwidth": 1},
                        "bar": {"color": hs_color_hex, "thickness": 0.25},
                        "bgcolor": "rgba(0,0,0,0)",
                        "steps": [
                            {"range": [0, 45], "color": "rgba(255,82,82,0.12)"},
                            {"range": [45, 70], "color": "rgba(255,176,32,0.10)"},
                            {"range": [70, 100], "color": "rgba(0,230,118,0.10)"},
                        ],
                        "threshold": {"line": {"color": hs_color_hex, "width": 3}, "value": hs2},
                    },
                    number={"font": {"color": hs_color_hex, "family": "JetBrains Mono", "size": 40}, "suffix": "/100"},
                    title={"text": "Portfolio Health Score", "font": {"color": "#94A3B8", "size": 12}},
                ))
                fig_hs.update_layout(
                    template="plotly_dark", height=220, paper_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=20,r=20,t=20,b=10), font=dict(color="#94A3B8"),
                )
                st.plotly_chart(fig_hs, use_container_width=True)

            with hg2:
                # Factor attribution bars
                st.markdown('<div class="sec-head" style="margin-top:8px;">Factor Attribution</div>', unsafe_allow_html=True)
                factors = [
                    ("Sharpe Quality", min(100, max(0, int((result.sharpe_ratio or 0) * 50))), "#00D4FF"),
                    ("Drawdown Control", min(100, max(0, int((1 - abs(result.walk_forward.max_drawdown if result.walk_forward and result.walk_forward.max_drawdown else 0.25)) * 100))), "#00E676"),
                    ("Sortino Quality", min(100, max(0, int((rm2.get("sortino") or 0) * 25))), "#818CF8"),
                    ("Regime Alignment", 80 if result.regime == "Bull" else (50 if result.regime == "Sideways" else 20), "#FFB020"),
                    ("Alpha Generation", min(100, max(0, int(((rm2.get("alpha") or 0) + 0.05) * 500))), "#00E676" if (rm2.get("alpha") or 0) > 0 else "#FF5252"),
                    ("TB Rate Adherence", 100, "#00D4FF"),
                ]
                for fname, fval, fcol in factors:
                    st.markdown(f"""
                    <div class="factor-row">
                      <div class="factor-name">{fname}</div>
                      <div class="factor-bar-bg">
                        <div class="factor-bar-fill" style="width:{fval}%;background:{fcol};"></div>
                      </div>
                      <div class="factor-val" style="color:{fcol};">{fval}</div>
                    </div>""", unsafe_allow_html=True)

        # ── Scenario Analysis ─────────────────────────────────────────────
        st.markdown('<div class="sec-head">Scenario Stress Testing · Historical & Hypothetical</div>', unsafe_allow_html=True)
        eq_w = result.equity_weight or 0.6
        fi_w = result.fi_weight or 0.4
        bz_sc = float(st.session_state["budget_zar"])

        sc_rows = []
        for sname, sp in SCENARIOS.items():
            eq_impact = bz_sc * eq_w * sp["equity_shock"]
            # Bond price approx: -duration * Δy (use 7y avg duration)
            bond_price_chg = -7.0 * (sp["bond_bp"] / 10000)
            fi_impact = bz_sc * fi_w * bond_price_chg
            fx_impact = bz_sc * sp["fx_shock"] * 0.3  # partial FX exposure
            total_impact = eq_impact + fi_impact + fx_impact
            pct_impact = total_impact / bz_sc if bz_sc > 0 else 0
            sc_rows.append({
                "Scenario": sname,
                "Type": sp["type"].upper(),
                "Equity Impact": _rand_raw(eq_impact),
                "Bond Impact": _rand_raw(fi_impact),
                "FX Impact": _rand_raw(fx_impact),
                "Total P&L": _rand_raw(total_impact),
                "% Return": f"{pct_impact:+.1%}",
                "Duration": f"{sp['months']} months",
                "Context": sp["desc"],
            })

        sc_df = pd.DataFrame(sc_rows)
        st.dataframe(sc_df, use_container_width=True, hide_index=True)

        # Scenario bar chart
        if _PLOTLY:
            sc_names = [r["Scenario"] for r in sc_rows]
            sc_vals = [float(r["Total P&L"].replace("R","").replace(",","")) for r in sc_rows]
            sc_cols = ["#00E676" if v >= 0 else "#FF5252" for v in sc_vals]
            fig_sc = go.Figure(go.Bar(
                x=sc_vals, y=sc_names, orientation="h",
                marker=dict(color=sc_cols),
                text=[f"{v/bz_sc*100:+.1f}%" if bz_sc > 0 else "" for v in sc_vals],
                textposition="outside",
                hovertemplate="%{y}<br>P&L: R%{x:,.0f}<extra></extra>",
            ))
            fig_sc.add_vline(x=0, line=dict(color="#4B5563", width=1))
            fig_sc.update_layout(
                template="plotly_dark", height=320, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0,r=80,t=10,b=10), xaxis_title="Stress-Test P&L (ZAR)",
                font=dict(family="Inter", color="#94A3B8"),
            )
            fig_sc.update_xaxes(gridcolor="rgba(255,255,255,0.04)")
            st.plotly_chart(fig_sc, use_container_width=True)

        # ── Risk disclosure ───────────────────────────────────────────────
        st.markdown("""
        <div style="background:rgba(255,176,32,0.05);border:1px solid rgba(255,176,32,0.15);border-radius:10px;padding:14px 18px;margin-top:16px;">
          <div style="font-size:0.72rem;color:#FFB020;font-weight:700;margin-bottom:4px;">⚠️ RISK DISCLAIMER</div>
          <div style="font-size:0.76rem;color:#94A3B8;line-height:1.6;">
            Scenario impacts are illustrative estimates based on historical analogues. Actual losses may be larger or smaller.
            VaR models assume normally distributed returns; fat tails and liquidity risk are not fully captured.
            This tool is for educational purposes only — not financial advice. Consult a licensed financial advisor before investing.
          </div>
        </div>""", unsafe_allow_html=True)


# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("""
<div style="text-align:center;color:#1F2937;font-size:0.72rem;margin-top:48px;padding-top:20px;border-top:1px solid rgba(255,255,255,0.05);">
  Atlas Capital Institutional Desk · Educational tool only — not financial advice ·
  Data via yfinance (free, delayed) · 10-Year TB Rate filter ≥4.45% ·
  Beta &amp; Treynor optimised · 25+ strategy signals
</div>
""", unsafe_allow_html=True)
