"""
app.py — Atlas Capital | Institutional Portfolio & Forex Dashboard v2
════════════════════════════════════════════════════════════════════════
Cinematic dark-mode Streamlit dashboard with:
  • Beta & Treynor Ratio per asset
  • 10-year TB rate filter (4.45%) — excludes underperforming assets
  • Strategy Lab with 25+ signal generators (SMC, Trend, Breakout, BTMM …)
  • Reactive sidebar — inputs apply immediately without re-clicking
  • Bulletproof PDF export (reportlab, None-safe throughout)
  • ZAR localisation for a South African user (default R300 budget)

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

# ── Plotly ────────────────────────────────────────────────────────────────────
try:
    import plotly.graph_objects as go
    _PLOTLY = True
except ImportError:
    _PLOTLY = False

# ── Engine imports ─────────────────────────────────────────────────────────────
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
    from strategies import (
        ALL_STRATEGIES, STRATEGY_CATEGORIES,
        run_all_strategies, aggregate_signal,
    )
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
# Page config
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
    --bg-0:#0a0e17; --bg-1:#0f1626; --bg-2:#161f33;
    --glass:rgba(255,255,255,0.04); --stroke:rgba(255,255,255,0.08);
    --gold:#f0b90b; --teal:#2dd4bf; --green:#34d399;
    --red:#f87171; --blue:#60a5fa; --purple:#a78bfa;
    --txt-1:#e8edf7; --txt-2:#8b97ad;
}
.stApp {
    background:
        radial-gradient(1200px 600px at 15% -5%,rgba(45,212,191,0.08),transparent 55%),
        radial-gradient(1000px 500px at 95% 0%,rgba(240,185,11,0.07),transparent 50%),
        linear-gradient(180deg,var(--bg-0) 0%,var(--bg-1) 100%);
    color:var(--txt-1); font-family:'Inter',sans-serif;
}
#MainMenu,footer,header{visibility:hidden;}
.block-container{padding-top:1.4rem;padding-bottom:3rem;max-width:1520px;}
section[data-testid="stSidebar"]{
    background:linear-gradient(180deg,var(--bg-1) 0%,var(--bg-0) 100%);
    border-right:1px solid var(--stroke);
}
section[data-testid="stSidebar"] *{color:var(--txt-1);}
.hero{
    border:1px solid var(--stroke); border-radius:18px;
    padding:22px 30px; margin-bottom:20px;
    background:linear-gradient(135deg,rgba(45,212,191,0.10),rgba(240,185,11,0.05)),var(--glass);
    backdrop-filter:blur(14px); box-shadow:0 8px 40px rgba(0,0,0,0.45);
}
.hero h1{
    font-size:1.9rem; font-weight:800; letter-spacing:-0.5px; margin:0;
    background:linear-gradient(90deg,#fff,var(--teal) 60%,var(--gold));
    -webkit-background-clip:text; -webkit-text-fill-color:transparent;
}
.hero p{color:var(--txt-2); margin:5px 0 0 0; font-size:0.93rem;}
.hero .pill{
    display:inline-block; margin-top:10px; padding:3px 11px; border-radius:99px;
    font-size:0.72rem; font-weight:600; letter-spacing:0.5px;
    background:rgba(52,211,153,0.12); color:var(--green); border:1px solid rgba(52,211,153,0.3);
}
.metric-card{
    border:1px solid var(--stroke); border-radius:14px; padding:16px 18px;
    background:var(--glass); backdrop-filter:blur(10px);
    transition:transform .18s,box-shadow .18s,border-color .18s; height:100%;
}
.metric-card:hover{transform:translateY(-3px); border-color:rgba(45,212,191,0.4); box-shadow:0 10px 30px rgba(45,212,191,0.10);}
.metric-card .label{font-size:0.70rem; text-transform:uppercase; letter-spacing:1px; color:var(--txt-2); font-weight:600;}
.metric-card .value{font-size:1.75rem; font-weight:800; margin-top:5px; font-family:'JetBrains Mono',monospace; line-height:1.1;}
.metric-card .sub{font-size:0.76rem; color:var(--txt-2); margin-top:3px;}
.v-gold{color:var(--gold);} .v-teal{color:var(--teal);} .v-green{color:var(--green);}
.v-red{color:var(--red);} .v-blue{color:var(--blue);} .v-white{color:var(--txt-1);} .v-purple{color:var(--purple);}
.section-title{
    font-size:1.10rem; font-weight:700; margin:22px 0 12px 0;
    display:flex; align-items:center; gap:10px;
}
.section-title::before{content:''; width:4px; height:18px; border-radius:4px; background:linear-gradient(180deg,var(--teal),var(--gold));}
.sig-card{border:1px solid var(--stroke); border-radius:12px; padding:14px 16px; background:var(--glass); margin-bottom:12px; backdrop-filter:blur(8px);}
.sig-card.long{border-left:4px solid var(--green);}
.sig-card.short{border-left:4px solid var(--red);}
.sig-card.flat{border-left:4px solid var(--txt-2);}
.sig-head{display:flex; justify-content:space-between; align-items:center;}
.sig-pair{font-size:1.05rem; font-weight:800; font-family:'JetBrains Mono',monospace;}
.badge{padding:2px 9px; border-radius:99px; font-size:0.70rem; font-weight:700; letter-spacing:0.5px;}
.badge.long{background:rgba(52,211,153,0.15); color:var(--green); border:1px solid rgba(52,211,153,0.35);}
.badge.short{background:rgba(248,113,113,0.15); color:var(--red); border:1px solid rgba(248,113,113,0.35);}
.badge.flat{background:rgba(139,151,173,0.15); color:var(--txt-2); border:1px solid rgba(139,151,173,0.35);}
.badge.rec{background:rgba(240,185,11,0.15); color:var(--gold); border:1px solid rgba(240,185,11,0.35);}
.sig-grid{display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-top:12px;}
.sig-cell .k{font-size:0.66rem; color:var(--txt-2); text-transform:uppercase; letter-spacing:0.5px;}
.sig-cell .val{font-size:0.95rem; font-weight:700; font-family:'JetBrains Mono',monospace; margin-top:2px;}
.rec-wrap{margin-top:12px; padding:10px 12px; border-radius:8px; background:rgba(240,185,11,0.06); border:1px solid rgba(240,185,11,0.2);}
.rec-bar-bg{height:7px; border-radius:99px; background:rgba(255,255,255,0.08); margin-top:7px; overflow:hidden;}
.rec-bar-fill{height:100%; border-radius:99px; background:linear-gradient(90deg,var(--gold),var(--red));}
.stTabs [data-baseweb="tab-list"]{gap:4px; border-bottom:1px solid var(--stroke);}
.stTabs [data-baseweb="tab"]{background:transparent; border-radius:10px 10px 0 0; padding:9px 18px; color:var(--txt-2); font-weight:600;}
.stTabs [aria-selected="true"]{background:var(--glass); color:var(--txt-1); border-bottom:2px solid var(--teal);}
.stDataFrame{border-radius:10px; overflow:hidden; border:1px solid var(--stroke);}
.stButton>button{
    background:linear-gradient(135deg,var(--teal),#1ba8a0); color:#04201d;
    font-weight:700; border:none; border-radius:10px; padding:9px 16px; transition:all .18s;
}
.stButton>button:hover{box-shadow:0 8px 24px rgba(45,212,191,0.35); transform:translateY(-1px);}
.strat-card{
    border:1px solid var(--stroke); border-radius:10px; padding:12px 14px;
    background:var(--glass); margin-bottom:8px;
}
.strat-card.long-card{border-left:3px solid var(--green);}
.strat-card.short-card{border-left:3px solid var(--red);}
.strat-card.flat-card{border-left:3px solid var(--txt-2);}
.strength-bar{height:5px; border-radius:99px; background:rgba(255,255,255,0.08); overflow:hidden; margin-top:4px;}
.strength-fill-long{height:100%; border-radius:99px; background:linear-gradient(90deg,#34d399,#059669);}
.strength-fill-short{height:100%; border-radius:99px; background:linear-gradient(90deg,#f87171,#dc2626);}
.strength-fill-flat{height:100%; border-radius:99px; background:var(--txt-2);}
</style>
"""
st.markdown(CINEMATIC_CSS, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _sf(value, spec: str, fallback: str = "—") -> str:
    if value is None:
        return fallback
    try:
        if isinstance(value, float) and math.isnan(value):
            return fallback
        return format(value, spec)
    except (TypeError, ValueError):
        return fallback


def metric_card(label: str, value: str, sub: str = "", color: str = "white") -> str:
    return f"""<div class="metric-card">
        <div class="label">{label}</div>
        <div class="value v-{color}">{value}</div>
        <div class="sub">{sub}</div>
    </div>"""


@st.cache_data(show_spinner=False, ttl=3600)
def get_usd_zar() -> float:
    try:
        import yfinance as yf
        df = yf.download("ZAR=X", period="5d", interval="1d", progress=False, auto_adjust=True)
        if not df.empty:
            val = float(df["Close"].dropna().iloc[-1])
            if 5 < val < 40:
                return val
    except Exception:
        pass
    return 18.5


def rand(amount) -> str:
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


# ══════════════════════════════════════════════════════════════════════════════
# Cached data loaders
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(show_spinner=False, ttl=1800)
def load_market_bundle(theme: str) -> Dict[str, Any]:
    if not _ENGINES:
        return {}
    theme_universe = {
        "Tech":     ["AAPL","MSFT","NVDA","GOOGL","META","AMD","AVGO","ORCL","CRM","TSM"],
        "Value":    ["BRK-B","JPM","XOM","CVX","JNJ","KO","PG","BAC","WFC","GS"],
        "Dividend": ["KO","PEP","JNJ","PG","MCD","MMM","T","VZ","O","PM"],
        "Emerging": ["EEM","VWO","INDA","EWZ","MCHI","EWY","EWT","EWJ","GXC","KWEB"],
        "Balanced": ["AAPL","MSFT","JPM","JNJ","KO","XOM","V","GOOGL","PG","HD"],
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
    try:
        import yfinance as yf
        df = yf.download("^GSPC", period="2y", interval="1d", progress=False, auto_adjust=True)
        if not df.empty:
            s = df["Close"]
            if isinstance(s, pd.DataFrame):
                s = s.iloc[:, 0]
            return s.dropna()
    except Exception:
        pass
    return None


def synthetic_bundle(theme: str) -> Dict[str, Any]:
    dates = pd.date_range(end=datetime.now(), periods=504, freq="B")
    rng = np.random.default_rng(7)

    def hist(seed, drift, vol, p0=120.0):
        r = np.random.default_rng(seed)
        price = p0 * np.cumprod(1 + r.normal(drift, vol, len(dates)))
        df = pd.DataFrame({"Open": price, "High": price*1.008, "Low": price*0.992,
                           "Close": price, "Volume": r.uniform(1e6, 5e6, len(dates))}, index=dates)
        df["Return"] = df["Close"].pct_change()
        return df

    eq_tickers = {
        "Tech":["AAPL","MSFT","NVDA","GOOGL","META","AMD"],
        "Value":["BRK-B","JPM","XOM","JNJ","KO","PG"],
        "Dividend":["KO","PEP","JNJ","PG","MCD","VZ"],
        "Emerging":["EEM","VWO","INDA","EWZ","MCHI","EWY"],
        "Balanced":["AAPL","MSFT","JPM","JNJ","KO","XOM"],
    }.get(theme, ["AAPL","MSFT","JPM","JNJ","KO","XOM"])

    histories = {t: hist(i, 0.0006, 0.015, 80+10*i) for i, t in enumerate(eq_tickers)}
    screened = pd.DataFrame({
        "ticker": eq_tickers[:4],
        "currentPrice": [float(histories[t]["Close"].iloc[-1]) for t in eq_tickers[:4]],
        "trailingPE": [12.3, 14.8, 9.5, 17.1],
        "priceToBook": [1.8, 2.4, 1.1, 2.9],
        "returnOnEquity": [0.21, 0.18, 0.25, 0.15],
        "earningsYield": [0.081, 0.068, 0.105, 0.058],
        "grahamMoS": [0.18, 0.12, 0.28, 0.09],
        "fcfYield": [0.06, 0.05, 0.08, 0.04],
        "compositeScore": [0.88, 0.81, 0.94, 0.72],
    })
    fi_tickers = ["SHY","IEF","TLT","AGG","LQD","HYG"]
    etf_hist = {t: hist(50+i, 0.0001, 0.004, 95+5*i) for i, t in enumerate(fi_tickers)}
    etf_yields = pd.DataFrame({
        "ticker": fi_tickers,
        "name": ["1-3Y Treasury","7-10Y Treasury","20+Y Treasury","US Aggregate","IG Corporate","High Yield"],
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
        d = pd.DataFrame({"Open": price, "High": price*1.004, "Low": price*0.996,
                          "Close": price, "Volume": np.zeros(len(dates))}, index=dates)
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
# Sidebar
# ══════════════════════════════════════════════════════════════════════════════

def _mark_stale():
    st.session_state["_stale"] = True


with st.sidebar:
    st.markdown(
        "<div style='font-size:1.25rem;font-weight:800;margin-bottom:4px;'>🛰️ Atlas Capital</div>"
        "<div style='color:#8b97ad;font-size:0.78rem;margin-bottom:16px;'>Portfolio & Forex Desk · v2</div>",
        unsafe_allow_html=True,
    )

    st.markdown("#### 💰 Investment Budget")
    if "budget_zar" not in st.session_state:
        st.session_state.budget_zar = 300.0

    budget_zar = st.number_input(
        "Amount (ZAR)", min_value=50.0, max_value=10_000_000.0,
        value=float(st.session_state.budget_zar), step=50.0, format="%.2f",
        help="Starting capital in South African Rand.",
        on_change=_mark_stale,
    )
    st.session_state.budget_zar = budget_zar

    qc = st.columns(4)
    for i, amt in enumerate([300, 1_000, 5_000, 25_000]):
        if qc[i].button(f"R{amt:,}", key=f"quick_{amt}"):
            st.session_state.budget_zar = float(amt)
            st.session_state["_stale"] = True
            st.rerun()

    st.divider()

    risk_appetite = st.select_slider(
        "🎯 Risk Appetite",
        options=["Conservative", "Moderate", "Aggressive"],
        value=st.session_state.get("_risk_appetite", "Moderate"),
        on_change=_mark_stale,
    )
    st.session_state["_risk_appetite"] = risk_appetite

    time_horizon = st.slider(
        "⏳ Time Horizon (months)", min_value=1, max_value=60,
        value=st.session_state.get("_time_horizon", 8),
        on_change=_mark_stale,
    )
    st.session_state["_time_horizon"] = time_horizon

    stock_type = st.selectbox(
        "📈 Preferred Stock Type",
        ["Value", "Tech", "Dividend", "Emerging", "Balanced"],
        index=["Value","Tech","Dividend","Emerging","Balanced"].index(
            st.session_state.get("_stock_type", "Value")
        ),
        on_change=_mark_stale,
    )
    st.session_state["_stock_type"] = stock_type

    target_return = st.slider(
        "🎁 Target Profit (%)", min_value=5, max_value=100,
        value=st.session_state.get("_target_return_pct", 20), step=5,
        on_change=_mark_stale,
    ) / 100.0
    st.session_state["_target_return_pct"] = int(target_return * 100)

    st.divider()
    use_live = st.toggle("🔴 Live market data", value=True,
                         help="Off = instant synthetic demo (offline-safe)")
    run_btn = st.button("⚡ Generate Strategy", use_container_width=True, type="primary")

    st.caption(f"USD/ZAR ≈ {get_usd_zar():.2f}  ·  {datetime.now():%Y-%m-%d %H:%M}")
    st.caption(f"TB Rate filter: ≥{RISK_FREE_RATE_10Y:.2%}")


# ── Session-wide values ────────────────────────────────────────────────────────
st.session_state.usd_zar = get_usd_zar()
usd_zar   = st.session_state.usd_zar
budget_usd = budget_zar / usd_zar

# ══════════════════════════════════════════════════════════════════════════════
# Hero header
# ══════════════════════════════════════════════════════════════════════════════

st.markdown(f"""
<div class="hero">
    <h1>Institutional Portfolio & Forex Desk</h1>
    <p>Regime-aware · Beta &amp; Treynor optimised · 25+ signal strategies · ATR forex with recovery sizing</p>
    <span class="pill">● LIVE · {datetime.now():%H:%M} · Budget {rand_raw(budget_zar)} (≈ ${budget_usd:,.2f}) · TB Filter ≥{RISK_FREE_RATE_10Y:.2%}</span>
</div>
""", unsafe_allow_html=True)

if not _ENGINES:
    st.error(f"⚠️ Engine modules not importable: {_ENGINE_ERROR}\n\n"
             "Ensure data_feed.py, portfolio_optimizer.py and forex_engine.py are in the same folder.")
if not _PLOTLY:
    st.warning("Plotly not installed — charts disabled. Run: pip install plotly")


# ══════════════════════════════════════════════════════════════════════════════
# Pipeline runner
# ══════════════════════════════════════════════════════════════════════════════

def build_everything() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    with st.status("🛰️ Booting strategy engine…", expanded=True) as status:
        st.write("📡 Fetching market data (equities · bonds · FX)…")
        if use_live and _ENGINES:
            bundle = load_market_bundle(stock_type)
            if not bundle or not bundle.get("equity", {}).get("histories"):
                st.write("⚠️ Live feed empty — switching to synthetic demo.")
                bundle = synthetic_bundle(stock_type)
        else:
            bundle = synthetic_bundle(stock_type)
        out["bundle"] = bundle

        st.write("🧮 Optimising portfolio (Beta · Treynor · Sharpe · Monte Carlo)…")
        if _ENGINES:
            req = PortfolioRequest(
                budget_usd=budget_usd, risk_appetite=risk_appetite,
                time_horizon_months=time_horizon,
                preferred_stock_type=stock_type, target_return=target_return,
                risk_free_rate=RISK_FREE_RATE_10Y,
            )
            opt = PortfolioOptimizer(req, bundle)
            result = opt.build()
            months_axis = sorted({3, 6, time_horizon, 12, 18, 24})
            targets_axis = [0.05, 0.10, 0.15, 0.20, 0.30, 0.50]
            matrix = opt.probability_matrix(months_axis, targets_axis)
            out["result"] = result
            out["prob_matrix"] = matrix
            st.session_state["last_regime"] = result.regime

        st.write("💱 Generating forex signals + recovery sizing…")
        if _ENGINES:
            signals, wf = run_forex_engine(
                bundle.get("forex", {}), account_equity=budget_usd, backtest=True,
            )
            out["signals"] = signals
            out["forex_wf"] = wf

        st.write("📊 Loading S&P 500 benchmark…")
        out["benchmark"] = load_benchmark() if use_live else None

        status.update(label="✅ Strategy ready", state="complete", expanded=False)
    return out


needs_rebuild = (
    run_btn
    or "engine_out" not in st.session_state
    or st.session_state.get("_stale", False)
)
if needs_rebuild:
    st.session_state["_stale"] = False
    st.session_state.engine_out = build_everything()

data        = st.session_state.engine_out
bundle      = data.get("bundle", {})
result      = data.get("result")
prob_matrix = data.get("prob_matrix", pd.DataFrame())
signals     = data.get("signals", [])
forex_wf    = data.get("forex_wf", {})
benchmark   = data.get("benchmark")

if bundle.get("meta", {}).get("synthetic"):
    st.info("ℹ️ Showing **synthetic demo data**. Toggle *Live market data* on and click *Generate Strategy* for real prices.")


# ══════════════════════════════════════════════════════════════════════════════
# Tabs
# ══════════════════════════════════════════════════════════════════════════════

tab_port, tab_fx, tab_strat, tab_fi = st.tabs([
    "  📊  Portfolio  ",
    "  💱  Forex Desk  ",
    "  🧠  Strategy Lab  ",
    "  🏦  Fixed Income  ",
])


# ──────────────────────────────────────────────────────────────────────────────
# TAB 1 — PORTFOLIO
# ──────────────────────────────────────────────────────────────────────────────

with tab_port:
    if result is None:
        st.warning("Run the engine to see portfolio output.")
    else:
        # ── PDF export ────────────────────────────────────────────────────
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
        regime_color = {"Bull":"green","Bear":"red","Sideways":"gold"}.get(result.regime, "white")
        c1.markdown(metric_card(
            "Market Regime", result.regime or "—",
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
        prob_color = "green" if (_prob and _prob >= 0.6) else ("gold" if (_prob and _prob >= 0.4) else "red")
        c4.markdown(metric_card(
            f"P(≥{target_return:.0%} profit)", _sf(_prob, '.0%'),
            f"holding {time_horizon} months", prob_color
        ), unsafe_allow_html=True)

        # ── TB rate info row ──────────────────────────────────────────────
        filtered_eq = [a for a in result.allocations if a.asset_class == "equity"]
        st.info(
            f"🏛️ **10-Year TB Rate Filter**: Assets must yield ≥ **{RISK_FREE_RATE_10Y:.2%}** annually to qualify. "
            f"Portfolio contains **{len(filtered_eq)} equity** positions that passed the screen."
        )

        # ── Probability headline banner ───────────────────────────────────
        st.markdown(f"""
        <div class="hero" style="margin-top:16px;">
            <h1 style="font-size:1.4rem;">
            Hold for {time_horizon} months → {_sf(result.monte_carlo_prob, '.0%')} likelihood of a {target_return:.0%}+ gain</h1>
            <p>Median: <b style="color:#34d399;">{_sf(result.mc_median_return, '+.1%')}</b>
            &nbsp;·&nbsp; Downside P10: <b style="color:#f87171;">{_sf(result.mc_p10, '+.1%')}</b>
            &nbsp;·&nbsp; Upside P90: <b style="color:#2dd4bf;">{_sf(result.mc_p90, '+.1%')}</b>
            &nbsp;·&nbsp; {result.request.monte_carlo_sims:,} Monte-Carlo paths</p>
        </div>
        """, unsafe_allow_html=True)

        # ── Probability matrix ────────────────────────────────────────────
        st.markdown('<div class="section-title">Probability Matrix · odds of hitting each target by hold time</div>',
                    unsafe_allow_html=True)
        if _PLOTLY and not prob_matrix.empty:
            z = prob_matrix.values * 100
            fig = go.Figure(go.Heatmap(
                z=z,
                x=[f"{m}m" for m in prob_matrix.columns],
                y=list(prob_matrix.index),
                colorscale=[[0,"#1a1f2e"],[0.4,"#7c5e10"],[0.7,"#caa017"],[1,"#34d399"]],
                text=[[f"{v:.0f}%" for v in row] for row in z],
                texttemplate="%{text}", textfont={"size":12,"family":"JetBrains Mono"},
                colorbar=dict(title="P(%)", tickfont=dict(color="#8b97ad")),
                hovertemplate="Hold %{x} · target %{y}<br>Probability: %{z:.0f}%<extra></extra>",
            ))
            fig.update_layout(
                template="plotly_dark", height=340,
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=10,r=10,t=10,b=10),
                xaxis_title="Holding period", yaxis_title="Target profit",
                font=dict(family="Inter", color="#e8edf7"),
            )
            st.plotly_chart(fig, use_container_width=True)
        elif prob_matrix.empty:
            st.info("Probability matrix unavailable (insufficient return history).")

        # ── Equity curve ──────────────────────────────────────────────────
        st.markdown('<div class="section-title">Simulated Equity Curve · strategy vs S&P 500 buy & hold</div>',
                    unsafe_allow_html=True)
        if _PLOTLY:
            hist_map = bundle.get("equity", {}).get("histories", {})
            blended, weights_sum = None, 0.0
            for a in result.allocations:
                if a.asset_class != "equity":
                    continue
                h = hist_map.get(a.ticker)
                if h is None or h.empty:
                    continue
                ret = h["Close"].pct_change().fillna(0)
                blended = ret * a.weight if blended is None else blended.add(ret * a.weight, fill_value=0)
                weights_sum += a.weight
            port_curve = None
            if blended is not None and weights_sum > 0:
                port_curve = (1 + blended / weights_sum).cumprod()
            fig2 = go.Figure()
            if port_curve is not None:
                pv = port_curve / port_curve.iloc[0] * budget_zar
                fig2.add_trace(go.Scatter(x=pv.index, y=pv.values, name="Atlas Strategy",
                    line=dict(color="#2dd4bf", width=2.5), fill="tozeroy", fillcolor="rgba(45,212,191,0.07)"))
            if benchmark is not None and not benchmark.empty:
                bench = benchmark / benchmark.iloc[0] * budget_zar
                fig2.add_trace(go.Scatter(x=bench.index, y=bench.values, name="S&P 500 Buy & Hold",
                    line=dict(color="#f0b90b", width=2, dash="dot")))
            elif port_curve is not None:
                rng2 = np.random.default_rng(1)
                synth = pd.Series(budget_zar * np.cumprod(1 + rng2.normal(0.0004, 0.011, len(port_curve))), index=port_curve.index)
                fig2.add_trace(go.Scatter(x=synth.index, y=synth.values, name="S&P 500 (proxy)", line=dict(color="#f0b90b", width=2, dash="dot")))
            fig2.update_layout(
                template="plotly_dark", height=360,
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=10,r=10,t=10,b=10),
                legend=dict(orientation="h", y=1.05, x=0, bgcolor="rgba(0,0,0,0)"),
                yaxis_title="Portfolio value (ZAR)", font=dict(family="Inter", color="#e8edf7"), hovermode="x unified",
            )
            fig2.update_yaxes(gridcolor="rgba(255,255,255,0.05)")
            fig2.update_xaxes(gridcolor="rgba(255,255,255,0.05)")
            st.plotly_chart(fig2, use_container_width=True)

        # ── Walk-forward ──────────────────────────────────────────────────
        if result.walk_forward and result.walk_forward.windows > 0:
            wf = result.walk_forward
            w1, w2, w3, w4 = st.columns(4)
            w1.markdown(metric_card("WF Windows", str(wf.windows), "out-of-sample", "white"), unsafe_allow_html=True)
            w2.markdown(metric_card("OOS Win Rate", _sf(wf.win_rate, '.0%'), "positive months", "green"), unsafe_allow_html=True)
            w3.markdown(metric_card("OOS Sharpe", _sf(wf.mean_oos_sharpe, '.2f'), "risk-adjusted", "teal"), unsafe_allow_html=True)
            w4.markdown(metric_card("Max Drawdown", _sf(wf.max_drawdown, '.1%'), "worst peak-trough", "red"), unsafe_allow_html=True)

        # ── Portfolio table with Beta & Treynor ───────────────────────────
        st.markdown('<div class="section-title">Recommended Portfolio · Beta · Treynor Ratio · TB-rate screened</div>',
                    unsafe_allow_html=True)
        alloc_rows = []
        for a in result.allocations:
            price_str = f"${a.price:,.2f}" if a.price else "—"
            passed_filter = a.asset_class != "equity" or (a.expected_return >= RISK_FREE_RATE_10Y)
            alloc_rows.append({
                "Ticker": a.ticker,
                "Class": "📈 Equity" if a.asset_class == "equity" else "🏦 Fixed Income",
                "Weight": _sf(a.weight, '.1%'),
                "Allocation (ZAR)": rand(a.dollar_amount),
                "Price": price_str,
                "Units": _sf(a.shares, '.4f'),
                "Exp. Return": _sf(a.expected_return, '.1%'),
                "Beta": _sf(a.beta, '.2f'),
                "Treynor": _sf(a.treynor_ratio, '.3f'),
                "TB Filter": "✅" if passed_filter else "❌",
                "Rationale": a.rationale,
            })
        st.dataframe(pd.DataFrame(alloc_rows), use_container_width=True, hide_index=True)

        cc1, cc2, cc3, cc4 = st.columns(4)
        cc1.markdown(metric_card("Total Invested", rand(result.total_invested), f"of {rand_raw(budget_zar)} budget", "gold"), unsafe_allow_html=True)
        cc2.markdown(metric_card("Expected Volatility", _sf(result.expected_volatility, '.1%'), "annualised", "blue"), unsafe_allow_html=True)
        avg_beta = np.mean([a.beta for a in result.allocations if a.beta]) if result.allocations else 1.0
        avg_treynor = np.mean([a.treynor_ratio for a in result.allocations if a.treynor_ratio]) if result.allocations else 0.0
        cc3.markdown(metric_card("Avg Portfolio Beta", _sf(avg_beta, '.2f'), "systematic risk", "purple"), unsafe_allow_html=True)
        cc4.markdown(metric_card("Avg Treynor Ratio", _sf(avg_treynor, '.3f'), "(E[R]-Rf)/Beta", "teal"), unsafe_allow_html=True)

        # ── Undervalued picks ─────────────────────────────────────────────
        st.markdown('<div class="section-title">Undervalued Picks · fundamental justification</div>', unsafe_allow_html=True)
        screened = bundle.get("equity", {}).get("screened_universe", pd.DataFrame())
        if not screened.empty:
            show_cols = {"ticker":"Ticker","currentPrice":"Price ($)","trailingPE":"P/E","priceToBook":"P/B",
                         "returnOnEquity":"ROE","earningsYield":"Earn. Yield","grahamMoS":"Graham MoS",
                         "fcfYield":"FCF Yield","compositeScore":"Value Score"}
            avail = [c for c in show_cols if c in screened.columns]
            disp = screened[avail].copy().rename(columns=show_cols)
            for pct_col in ["ROE","Earn. Yield","Graham MoS","FCF Yield"]:
                if pct_col in disp.columns:
                    disp[pct_col] = disp[pct_col].apply(lambda v: f"{v:.1%}" if pd.notna(v) else "—")
            for num_col in ["P/E","P/B","Value Score"]:
                if num_col in disp.columns:
                    disp[num_col] = disp[num_col].apply(lambda v: f"{v:.2f}" if pd.notna(v) else "—")
            if "Price ($)" in disp.columns:
                disp["Price ($)"] = disp["Price ($)"].apply(lambda v: f"${v:,.2f}" if pd.notna(v) else "—")
            st.dataframe(disp, use_container_width=True, hide_index=True)
        else:
            st.info("No stocks passed the value screen for this universe.")


# ──────────────────────────────────────────────────────────────────────────────
# TAB 2 — FOREX DESK
# ──────────────────────────────────────────────────────────────────────────────

with tab_fx:
    st.markdown('<div class="section-title">Live Signal Feed · session-timed entries with ATR risk control</div>',
                unsafe_allow_html=True)
    if not signals:
        st.info("No actionable forex signals right now. Signals require trend, momentum, session-timing and volatility filters to all align.")
    else:
        longs = sum(1 for s in signals if s.direction == "LONG")
        shorts = sum(1 for s in signals if s.direction == "SHORT")
        rec_active = sum(1 for s in signals if s.recovery_mode)
        f1, f2, f3, f4 = st.columns(4)
        f1.markdown(metric_card("Active Signals", str(len(signals)), "across major pairs", "teal"), unsafe_allow_html=True)
        f2.markdown(metric_card("Long / Short", f"{longs} / {shorts}", "directional bias", "blue"), unsafe_allow_html=True)
        f3.markdown(metric_card("Recovery Mode", str(rec_active), "trades recovering losses", "gold"), unsafe_allow_html=True)
        avg_conf = np.mean([s.confidence for s in signals]) if signals else 0
        f4.markdown(metric_card("Avg Confidence", f"{avg_conf:.0%}", "filter agreement", "green"), unsafe_allow_html=True)

        if _SIGNAL_EXPORT:
            ec1, ec2 = st.columns([3, 1])
            with ec2:
                if st.button("📡 Export to MT5", use_container_width=True, help="Write atlas_signals.csv/.json for the MQL5 EA"):
                    try:
                        path = export_signals(signals, account_equity=budget_usd)
                        st.success(f"Exported → {path}")
                    except Exception as exc:
                        st.error(f"Export failed: {exc}")

        st.markdown("<br>", unsafe_allow_html=True)
        for s in sorted(signals, key=lambda x: x.confidence, reverse=True):
            dir_cls = "long" if s.direction == "LONG" else "short"
            entry_str = f"{s.entry_window_utc[0]:02d}:00–{s.entry_window_utc[1]:02d}:00 UTC"
            exit_str  = f"{s.exit_window_utc[0]:02d}:00–{s.exit_window_utc[1]:02d}:00 UTC"
            rec_badge = '<span class="badge rec">⟳ RECOVERY</span>' if s.recovery_mode else ''
            card = f"""<div class="sig-card {dir_cls}">
              <div class="sig-head">
                <span class="sig-pair">{s.pair}</span>
                <span>
                  <span class="badge {dir_cls}">{s.direction}</span>
                  {rec_badge}
                  <span style="color:#8b97ad;font-size:0.78rem;margin-left:8px;">{s.regime} · {s.confidence:.0%} conf</span>
                </span>
              </div>
              <div class="sig-grid">
                <div class="sig-cell"><div class="k">⏱ Entry</div><div class="val v-teal">{entry_str}</div></div>
                <div class="sig-cell"><div class="k">🚪 Exit</div><div class="val v-blue">{exit_str}</div></div>
                <div class="sig-cell"><div class="k">Entry Price</div><div class="val">{s.entry_price}</div></div>
                <div class="sig-cell"><div class="k">RR Ratio</div><div class="val v-gold">1 : {_sf(s.risk_reward, '.2f')}</div></div>
                <div class="sig-cell"><div class="k">🛑 Stop Loss</div><div class="val v-red">{s.stop_loss}</div></div>
                <div class="sig-cell"><div class="k">🎯 Take Profit</div><div class="val v-green">{s.take_profit}</div></div>
                <div class="sig-cell"><div class="k">Lot Size</div><div class="val">{s.lot_size}</div></div>
                <div class="sig-cell"><div class="k">Risk (ZAR)</div><div class="val">{rand(s.dollar_risk)}</div></div>
              </div>"""
            if s.recovery_mode and s.recovery_deficit > 0:
                base_risk = budget_usd * 0.01
                mult = min(s.recovery_deficit / base_risk + 1, 3.0) if base_risk > 0 else 1.0
                fill_pct = min(mult / 3.0 * 100, 100)
                card += f"""<div class="rec-wrap">
                  <div style="display:flex;justify-content:space-between;font-size:0.82rem;">
                    <span style="color:#f0b90b;font-weight:700;">⟳ Recovery Sizing Active</span>
                    <span style="font-family:'JetBrains Mono';">×{mult:.2f} multiplier · deficit {rand(s.recovery_deficit)}</span>
                  </div>
                  <div class="rec-bar-bg"><div class="rec-bar-fill" style="width:{fill_pct:.0f}%;"></div></div>
                  <div style="color:#8b97ad;font-size:0.72rem;margin-top:5px;">Capped at ×3.0 · 15% drawdown circuit-breaker</div>
                </div>"""
            card += "</div>"
            st.markdown(card, unsafe_allow_html=True)

    with st.expander("ℹ️ How Recovery Sizing works"):
        st.markdown("""
        The **RecoverySizer** makes back lost trades while preventing wipe-out:
        1. **Base trade** risks 1% of equity on the stop distance.
        2. After a stop-out, the dollar **deficit accumulates**.
        3. The next trade is sized so one TP win recovers the full deficit + normal base profit:
           `recovery_lot = (deficit + base_risk) / (tp_distance × pip_value)`
        4. Hard-capped at **×3.0** base — controlled, not blind Martingale.
        5. **Circuit-breaker** resets deficit if equity drawdown hits 15%.
        """)

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
# TAB 3 — STRATEGY LAB
# ──────────────────────────────────────────────────────────────────────────────

with tab_strat:
    st.markdown('<div class="section-title">Strategy Lab · 25+ signal generators across Trend, SMC, Breakout & more</div>',
                unsafe_allow_html=True)

    if not _STRATEGIES:
        st.error("strategies.py not found — run the engine to generate the strategies module.")
    else:
        # Pick ticker to analyse
        hist_map = bundle.get("equity", {}).get("histories", {})
        fx_map   = bundle.get("forex", {}).get("daily", {})
        all_tickers = list(hist_map.keys()) + list(fx_map.keys())

        if not all_tickers:
            st.info("No price data available. Generate the strategy first.")
        else:
            stcol1, stcol2, stcol3 = st.columns([2, 2, 2])
            selected_ticker = stcol1.selectbox("📈 Analyse ticker", all_tickers, key="strat_ticker")
            selected_cats = stcol2.multiselect(
                "Filter by category",
                STRATEGY_CATEGORIES,
                default=STRATEGY_CATEGORIES,
                key="strat_cats",
            )
            show_flat = stcol3.checkbox("Show FLAT signals", value=False)

            if selected_ticker in hist_map:
                df_strat = hist_map[selected_ticker].copy()
            else:
                df_strat = fx_map.get(selected_ticker, pd.DataFrame()).copy()

            if df_strat.empty or len(df_strat) < 15:
                st.warning("Insufficient data for this ticker.")
            else:
                # Run strategies
                selected_strategy_names = [s.name for s in ALL_STRATEGIES if s.category in selected_cats]
                sig_list = run_all_strategies(df_strat, selected_names=selected_strategy_names)
                consensus = aggregate_signal(sig_list)

                # ── Consensus card ────────────────────────────────────────
                con_col1, con_col2, con_col3, con_col4 = st.columns(4)
                con_dir = consensus["direction"]
                con_color = "green" if con_dir == "LONG" else ("red" if con_dir == "SHORT" else "white")
                con_col1.markdown(metric_card("Consensus Signal", con_dir, f"score={consensus['score']:+.2f}", con_color), unsafe_allow_html=True)
                con_col2.markdown(metric_card("Confidence", f"{consensus['confidence']:.0%}", "signal agreement", "teal"), unsafe_allow_html=True)
                con_col3.markdown(metric_card("Long / Short", f"{consensus['long_count']} / {consensus['short_count']}", f"Flat: {consensus['flat_count']}", "blue"), unsafe_allow_html=True)
                long_score = consensus.get("long_score", 0)
                short_score = consensus.get("short_score", 0)
                con_col4.markdown(metric_card("Score Balance", f"{long_score:.1f}L / {short_score:.1f}S", "weighted signal strength", "gold"), unsafe_allow_html=True)

                # ── Consensus gauge ───────────────────────────────────────
                if _PLOTLY and sig_list:
                    gauge_val = (consensus["score"] + 1) / 2 * 100  # map -1..1 → 0..100
                    fig_g = go.Figure(go.Indicator(
                        mode="gauge+number",
                        value=gauge_val,
                        domain={"x": [0, 1], "y": [0, 1]},
                        gauge={
                            "axis": {"range": [0, 100], "tickcolor": "#8b97ad"},
                            "bar": {"color": "#2dd4bf"},
                            "steps": [
                                {"range": [0, 35], "color": "rgba(248,113,113,0.3)"},
                                {"range": [35, 65], "color": "rgba(139,151,173,0.2)"},
                                {"range": [65, 100], "color": "rgba(52,211,153,0.3)"},
                            ],
                            "threshold": {"line": {"color": "#f0b90b", "width": 3}, "value": gauge_val},
                            "bgcolor": "rgba(0,0,0,0)",
                        },
                        number={"font": {"color": "#e8edf7", "family": "JetBrains Mono"}, "suffix": "%"},
                        title={"text": f"Bullish Consensus · {selected_ticker}", "font": {"color": "#8b97ad"}},
                    ))
                    fig_g.update_layout(
                        template="plotly_dark", height=220,
                        paper_bgcolor="rgba(0,0,0,0)", margin=dict(l=30, r=30, t=40, b=10),
                        font=dict(color="#e8edf7"),
                    )
                    st.plotly_chart(fig_g, use_container_width=True)

                # ── Individual strategy cards ─────────────────────────────
                st.markdown(f'<div class="section-title">Individual Signals — {selected_ticker} ({len(sig_list)} strategies)</div>',
                            unsafe_allow_html=True)

                cat_groups: Dict[str, List] = {}
                for sig in sig_list:
                    if not show_flat and sig.direction == "FLAT":
                        continue
                    cat_groups.setdefault(sig.category, []).append(sig)

                if not cat_groups:
                    st.info("No non-FLAT signals. Enable 'Show FLAT signals' to see all.")

                for cat, cat_sigs in cat_groups.items():
                    st.markdown(f"**{cat}**")
                    cols = st.columns(min(3, len(cat_sigs)))
                    for idx, sig in enumerate(cat_sigs):
                        col = cols[idx % len(cols)]
                        dir_class = "long" if sig.direction == "LONG" else ("short" if sig.direction == "SHORT" else "flat")
                        dir_color = "v-green" if sig.direction == "LONG" else ("v-red" if sig.direction == "SHORT" else "v-white")
                        fill_class = f"strength-fill-{dir_class}"
                        fill_pct = int(sig.strength * 100)
                        col.markdown(f"""
                        <div class="strat-card {dir_class}-card">
                            <div style="font-size:0.72rem;color:#8b97ad;font-weight:600;">{sig.strategy}</div>
                            <div class="badge {dir_class}" style="margin-top:4px;">{sig.direction}</div>
                            <div class="strength-bar"><div class="{fill_class}" style="width:{fill_pct}%;"></div></div>
                            <div style="font-size:0.70rem;color:#8b97ad;margin-top:5px;">{sig.detail[:80]}{"…" if len(sig.detail) > 80 else ""}</div>
                        </div>""", unsafe_allow_html=True)

                # ── Price chart with key levels ────────────────────────────
                if _PLOTLY and not df_strat.empty:
                    st.markdown('<div class="section-title">Price Chart · recent 90 sessions</div>', unsafe_allow_html=True)
                    chart_df = df_strat.tail(90)
                    fig_c = go.Figure()
                    fig_c.add_trace(go.Candlestick(
                        x=chart_df.index,
                        open=chart_df["Open"], high=chart_df["High"],
                        low=chart_df["Low"], close=chart_df["Close"],
                        increasing=dict(line=dict(color="#34d399"), fillcolor="rgba(52,211,153,0.3)"),
                        decreasing=dict(line=dict(color="#f87171"), fillcolor="rgba(248,113,113,0.3)"),
                        name=selected_ticker,
                    ))
                    # EMA overlays
                    for p, col in [(20, "#2dd4bf"), (50, "#f0b90b"), (200, "#a78bfa")]:
                        ema = chart_df["Close"].ewm(span=p).mean()
                        fig_c.add_trace(go.Scatter(x=chart_df.index, y=ema, name=f"EMA{p}",
                            line=dict(color=col, width=1.2, dash="dot")))
                    fig_c.update_layout(
                        template="plotly_dark", height=420,
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        margin=dict(l=10, r=10, t=10, b=10),
                        xaxis_rangeslider_visible=False,
                        legend=dict(orientation="h", y=1.05, x=0, bgcolor="rgba(0,0,0,0)"),
                        font=dict(family="Inter", color="#e8edf7"),
                    )
                    fig_c.update_xaxes(gridcolor="rgba(255,255,255,0.05)")
                    fig_c.update_yaxes(gridcolor="rgba(255,255,255,0.05)")
                    st.plotly_chart(fig_c, use_container_width=True)


# ──────────────────────────────────────────────────────────────────────────────
# TAB 4 — FIXED INCOME
# ──────────────────────────────────────────────────────────────────────────────

with tab_fi:
    fi = bundle.get("fixed_income", {})
    st.markdown('<div class="section-title">Fixed-Income Allocation · yield to maturity</div>', unsafe_allow_html=True)

    etf_yields = fi.get("etf_yields", pd.DataFrame())
    if not etf_yields.empty and "ytm_proxy" in etf_yields.columns:
        valid = etf_yields.dropna(subset=["ytm_proxy"])
        if not valid.empty:
            top = valid.sort_values("ytm_proxy", ascending=False).head(4)
            cols = st.columns(len(top))
            for col, (_, row) in zip(cols, top.iterrows()):
                col.markdown(metric_card(
                    row["ticker"], f"{row['ytm_proxy']:.2%}",
                    f"{row.get('name','bond ETF')} · YTM", "gold"
                ), unsafe_allow_html=True)

        if _PLOTLY and not valid.empty:
            v = valid.sort_values("ytm_proxy")
            fig = go.Figure(go.Bar(
                x=v["ytm_proxy"]*100, y=v["ticker"], orientation="h",
                marker=dict(color=v["ytm_proxy"]*100, colorscale=[[0,"#1ba8a0"],[1,"#f0b90b"]]),
                text=[f"{y:.2%}" for y in v["ytm_proxy"]], textposition="outside",
                hovertemplate="%{y}: %{x:.2f}%<extra></extra>",
            ))
            fig.update_layout(
                template="plotly_dark", height=280,
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=10,r=40,t=10,b=10), xaxis_title="Yield to Maturity (%)",
                font=dict(family="Inter", color="#e8edf7"),
            )
            fig.update_xaxes(gridcolor="rgba(255,255,255,0.05)")
            st.plotly_chart(fig, use_container_width=True)

        cols_map = {"ticker":"Ticker","name":"Fund","ytm_proxy":"YTM","ytm_source":"YTM Source","duration_years":"Duration (yrs)"}
        avail = [c for c in cols_map if c in etf_yields.columns]
        disp = etf_yields[avail].rename(columns=cols_map)
        if "YTM" in disp.columns:
            disp["YTM"] = disp["YTM"].apply(lambda v: f"{v:.2%}" if pd.notna(v) else "—")
        if "Duration (yrs)" in disp.columns:
            disp["Duration (yrs)"] = disp["Duration (yrs)"].apply(lambda v: f"{v:.1f}" if pd.notna(v) else "—")
        st.dataframe(disp, use_container_width=True, hide_index=True)
    else:
        st.info("Fixed-income YTM data unavailable for this run.")

    curve = fi.get("yield_curve", pd.Series(dtype=float))
    slope = fi.get("curve_slope_bp")
    if curve is not None and not curve.empty:
        st.markdown('<div class="section-title">US Treasury Yield Curve</div>', unsafe_allow_html=True)
        cc1, cc2 = st.columns([2, 1])
        if _PLOTLY:
            fig = go.Figure(go.Scatter(
                x=list(curve.index), y=[v*100 for v in curve.values],
                mode="lines+markers", line=dict(color="#2dd4bf", width=3),
                marker=dict(size=10, color="#f0b90b"),
                fill="tozeroy", fillcolor="rgba(45,212,191,0.08)",
            ))
            fig.update_layout(
                template="plotly_dark", height=280,
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=10,r=10,t=10,b=10), yaxis_title="Yield (%)", xaxis_title="Maturity",
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
                "Curve Read", "Defensive" if inverted else "Risk-on",
                "tilt fixed income longer" if inverted else "equities favoured",
                "gold" if inverted else "teal"
            ), unsafe_allow_html=True)


# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown(
    "<div style='text-align:center;color:#5a6478;font-size:0.74rem;margin-top:40px;'>"
    "Atlas Capital Desk · Educational tool, not financial advice · "
    "Data via yfinance · TB Rate filter ≥4.45% · Beta &amp; Treynor optimised · "
    "25+ strategy signals (SMC · BTMM · Turtle · Seasonal)<br>"
    "⬅ Sidebar collapsed? Click the <b>&gt;</b> arrow on the far left edge of the screen."
    "</div>",
    unsafe_allow_html=True,
)
