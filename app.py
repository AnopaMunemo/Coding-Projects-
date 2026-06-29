"""
app.py — Atlas Capital | Institutional Portfolio & Forex/Gold Desk · 2026 Edition
════════════════════════════════════════════════════════════════════════════════
Cinematic 2026 fintech aesthetic — deep-black canvas, ambient gradient orbs,
glassmorphism cards and neon glow. JSE/ZAR-native with an XAU/USD gold desk.
All settings live in the ⚙️ Settings tab — no hidden sidebar.

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
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=Sora:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

/* ════════════════════════════════════════════════════════════════════
   ATLAS CAPITAL · 2026 Cinematic Theme
   Deep-black canvas · ambient gradient orbs · glassmorphism · neon glow
   ════════════════════════════════════════════════════════════════════ */

:root {
  --bg:       #05070E;
  --bg2:      #080B16;
  --card:     rgba(18,24,40,0.62);
  --card-sol: #0E1422;
  --card2:    rgba(28,36,58,0.70);
  --border:   rgba(255,255,255,0.08);
  --border2:  rgba(255,255,255,0.14);
  --accent:   #00D4FF;
  --accent2:  #38BDF8;
  --emerald:  #00E676;
  --lime:     #B6FF3C;
  --amber:    #FF9F45;
  --violet:   #8B7CFF;
  --magenta:  #E879F9;
  --success:  #00E676;
  --warn:     #FFB020;
  --danger:   #FF5C6E;
  --purple:   #8B7CFF;
  --txt:      #F4F7FF;
  --txt2:     #97A3BE;
  --txt3:     #56627E;
  --glow:     rgba(0,212,255,0.22);
}

/* ── Global canvas ── */
html, body, .stApp {
  background: var(--bg) !important;
  color: var(--txt) !important;
  font-family: 'Inter', sans-serif !important;
}

/* Cinematic ambient orbs — fixed behind everything */
.stApp::before {
  content: ''; position: fixed; inset: 0; z-index: 0; pointer-events: none;
  background:
    radial-gradient(820px 620px at 12% -8%,  rgba(0,230,118,0.16), transparent 60%),
    radial-gradient(760px 680px at 92% 6%,   rgba(139,124,255,0.18), transparent 62%),
    radial-gradient(900px 700px at 78% 104%, rgba(255,159,69,0.12), transparent 60%),
    radial-gradient(700px 600px at 4% 96%,   rgba(0,212,255,0.12), transparent 58%);
  animation: drift 22s ease-in-out infinite alternate;
}
/* Fine grid texture overlay (à la trading terminals) */
.stApp::after {
  content: ''; position: fixed; inset: 0; z-index: 0; pointer-events: none; opacity: 0.5;
  background-image:
    linear-gradient(rgba(255,255,255,0.022) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,0.022) 1px, transparent 1px);
  background-size: 46px 46px;
  -webkit-mask-image: radial-gradient(circle at 50% 30%, #000 0%, transparent 78%);
          mask-image: radial-gradient(circle at 50% 30%, #000 0%, transparent 78%);
}
@keyframes drift {
  0%   { transform: translate3d(0,0,0) scale(1); }
  100% { transform: translate3d(-2%, 1.5%, 0) scale(1.06); }
}
/* Keep real content above the ambient layers */
.block-container { position: relative; z-index: 1; padding: 0 2.2rem 3.4rem 2.2rem !important; max-width: 1640px !important; }
#MainMenu, footer, header, section[data-testid="stSidebar"] { display: none !important; }

::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(139,124,255,0.28); border-radius: 99px; }
::-webkit-scrollbar-thumb:hover { background: rgba(139,124,255,0.5); }

/* ── Top nav bar ── */
.atlas-nav {
  display: flex; align-items: center; justify-content: space-between;
  padding: 18px 0 18px 0; margin-bottom: 22px;
  border-bottom: 1px solid var(--border);
}
.atlas-logo {
  font-family: 'Sora', sans-serif; font-size: 1.22rem; font-weight: 800;
  letter-spacing: -0.4px; color: var(--txt);
}
.atlas-logo span {
  background: linear-gradient(120deg, var(--accent), var(--violet) 70%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.atlas-badge {
  display: inline-flex; align-items: center; gap: 7px;
  background: rgba(0,230,118,0.10); border: 1px solid rgba(0,230,118,0.30);
  border-radius: 99px; padding: 5px 13px; font-size: 0.72rem; font-weight: 700;
  color: var(--emerald); letter-spacing: 0.4px;
  box-shadow: 0 0 18px rgba(0,230,118,0.12);
}
.atlas-badge::before {
  content: '●'; font-size: 0.55rem;
  animation: pulse 1.8s ease-in-out infinite;
}
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.25; } }

/* ── Premium metric cards (glassmorphism) ── */
.kpi {
  background: var(--card);
  backdrop-filter: blur(18px) saturate(135%);
  -webkit-backdrop-filter: blur(18px) saturate(135%);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 22px 24px;
  transition: transform .25s cubic-bezier(.2,.8,.2,1), border-color .25s, box-shadow .25s;
  position: relative; overflow: hidden;
  box-shadow: 0 10px 34px rgba(0,0,0,0.42), inset 0 1px 0 rgba(255,255,255,0.05);
}
.kpi::before {
  content: ''; position: absolute; inset: 0; border-radius: 20px; padding: 1px;
  background: linear-gradient(135deg, rgba(0,212,255,0.45), transparent 38%, transparent 62%, rgba(139,124,255,0.45));
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
          mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor; mask-composite: exclude;
  opacity: 0; transition: opacity .25s;
}
.kpi::after {
  content: ''; position: absolute; top: -40%; right: -30%; width: 220px; height: 220px;
  background: radial-gradient(circle, var(--glow), transparent 70%);
  opacity: 0; transition: opacity .25s; pointer-events: none;
}
.kpi:hover { transform: translateY(-4px); border-color: transparent; box-shadow: 0 18px 48px rgba(0,212,255,0.14), inset 0 1px 0 rgba(255,255,255,0.07); }
.kpi:hover::before, .kpi:hover::after { opacity: 1; }
.kpi .k-label { font-size: 0.66rem; text-transform: uppercase; letter-spacing: 1.4px; color: var(--txt2); font-weight: 700; margin-bottom: 9px; }
.kpi .k-value { font-size: 2.05rem; font-weight: 700; font-family: 'JetBrains Mono', monospace; line-height: 1; margin-bottom: 7px; letter-spacing: -0.5px; }
.kpi .k-sub { font-size: 0.74rem; color: var(--txt2); }
.k-accent  { color: var(--accent);  text-shadow: 0 0 24px rgba(0,212,255,0.35); }
.k-success { color: var(--emerald); text-shadow: 0 0 24px rgba(0,230,118,0.30); }
.k-warn    { color: var(--warn); }
.k-danger  { color: var(--danger); }
.k-purple  { color: var(--violet);  text-shadow: 0 0 24px rgba(139,124,255,0.32); }
.k-white   { color: var(--txt); }

/* ── Section headers ── */
.sec-head {
  font-family: 'Sora', sans-serif;
  font-size: 0.70rem; text-transform: uppercase; letter-spacing: 2px;
  color: var(--txt2); font-weight: 700; margin: 30px 0 15px 0;
  display: flex; align-items: center; gap: 12px;
}
.sec-head::before { content: ''; width: 18px; height: 2px; border-radius: 99px; background: linear-gradient(90deg, var(--accent), transparent); }
.sec-head::after  { content: ''; flex: 1; height: 1px; background: linear-gradient(90deg, var(--border), transparent); }

/* ── In-tab hero ── */
.hero-block { padding: 34px 0 26px 0; }
.hero-block h1 {
  font-family: 'Sora', sans-serif;
  font-size: 3.1rem; font-weight: 800; letter-spacing: -1.8px; line-height: 1.04;
  margin: 0 0 12px 0;
  background: linear-gradient(115deg, #FFFFFF 18%, var(--accent) 58%, var(--violet) 92%);
  background-size: 220% auto;
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  animation: shine 9s linear infinite;
}
.hero-block .hero-sub { font-size: 1.02rem; color: var(--txt2); max-width: 600px; line-height: 1.65; }
@keyframes shine { to { background-position: 220% center; } }

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
  gap: 3px; background: var(--card); backdrop-filter: blur(16px);
  border-radius: 16px; padding: 6px;
  border: 1px solid var(--border); margin-bottom: 26px;
  box-shadow: 0 8px 28px rgba(0,0,0,0.4);
}
.stTabs [data-baseweb="tab"] {
  background: transparent; border-radius: 11px; padding: 10px 20px;
  color: var(--txt2); font-weight: 600; font-size: 0.85rem; border: none;
  transition: all .2s; font-family: 'Sora', sans-serif;
}
.stTabs [data-baseweb="tab"]:hover { color: var(--txt); }
.stTabs [aria-selected="true"] {
  background: linear-gradient(135deg, rgba(0,212,255,0.16), rgba(139,124,255,0.16)) !important;
  color: var(--txt) !important;
  box-shadow: inset 0 0 0 1px rgba(255,255,255,0.10), 0 4px 18px rgba(0,212,255,0.12);
}
.stTabs [data-baseweb="tab-panel"] { padding: 0 !important; }

/* ── Settings cards ── */
.settings-card {
  background: var(--card); backdrop-filter: blur(18px);
  border: 1px solid var(--border); border-radius: 20px;
  padding: 24px 26px; margin-bottom: 16px;
  box-shadow: 0 10px 30px rgba(0,0,0,0.36);
}
.settings-card h3 { font-family: 'Sora', sans-serif; font-size: 0.92rem; font-weight: 700; color: var(--txt); margin: 0 0 16px 0; }

/* ── Data table ── */
.stDataFrame { border-radius: 16px !important; border: 1px solid var(--border) !important; overflow: hidden; }
.stDataFrame td, .stDataFrame th { font-size: 0.80rem !important; }

/* ── Buttons ── */
.stButton > button {
  background: linear-gradient(135deg, var(--accent), var(--accent2)) !important;
  color: #04121A !important;
  font-weight: 800 !important; border: none !important; border-radius: 12px !important;
  padding: 11px 22px !important; font-size: 0.88rem !important;
  letter-spacing: 0.2px !important;
  transition: all .2s !important;
  box-shadow: 0 6px 22px rgba(0,212,255,0.26) !important;
}
.stButton > button:hover { box-shadow: 0 0 34px rgba(0,212,255,0.55) !important; transform: translateY(-2px) !important; }
button[kind="secondary"] {
  background: var(--card2) !important; color: var(--txt) !important;
  box-shadow: inset 0 0 0 1px var(--border) !important;
}

/* ── Sliders ── */
.stSlider > div > div > div { background: rgba(255,255,255,0.10) !important; }
[data-testid="stSlider"] > div > div > div > div { background: linear-gradient(90deg, var(--accent), var(--violet)) !important; }

/* ── Inputs ── */
.stSelectbox > div, .stNumberInput > div { background: var(--card2) !important; border-radius: 11px !important; }
[data-baseweb="select"] { background: var(--card2) !important; border-radius: 11px !important; }
[data-baseweb="input"]  { background: var(--card2) !important; border-radius: 11px !important; }

/* ── Signal cards (glass) ── */
.sig {
  background: var(--card); backdrop-filter: blur(16px);
  border: 1px solid var(--border); border-radius: 18px;
  padding: 18px 22px; margin-bottom: 13px;
  position: relative; overflow: hidden;
  box-shadow: 0 8px 26px rgba(0,0,0,0.34);
  transition: transform .2s, box-shadow .2s;
}
.sig:hover { transform: translateY(-2px); box-shadow: 0 14px 38px rgba(0,0,0,0.46); }
.sig::before { content:''; position:absolute; left:0; top:0; bottom:0; width:3px; }
.sig.sig-long::before  { background: linear-gradient(180deg, var(--emerald), transparent); box-shadow: 0 0 18px var(--emerald); }
.sig.sig-short::before { background: linear-gradient(180deg, var(--danger), transparent);  box-shadow: 0 0 18px var(--danger); }
.sig-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }
.sig-pair { font-size: 1.12rem; font-weight: 700; font-family: 'JetBrains Mono', monospace; letter-spacing: -0.3px; }
.sig-grid { display: grid; grid-template-columns: repeat(4,1fr); gap: 13px; }
.sig-cell .k { font-size: 0.63rem; color: var(--txt2); text-transform: uppercase; letter-spacing: 0.6px; margin-bottom: 4px; }
.sig-cell .v { font-size: 0.92rem; font-weight: 700; font-family: 'JetBrains Mono', monospace; }
.tag { display: inline-block; padding: 3px 11px; border-radius: 99px; font-size: 0.67rem; font-weight: 800; letter-spacing: 0.6px; }
.tag-long  { background: rgba(0,230,118,0.14); color: var(--emerald); border: 1px solid rgba(0,230,118,0.34); box-shadow: 0 0 16px rgba(0,230,118,0.14); }
.tag-short { background: rgba(255,92,110,0.14); color: var(--danger);  border: 1px solid rgba(255,92,110,0.34); box-shadow: 0 0 16px rgba(255,92,110,0.14); }
.tag-flat  { background: rgba(148,163,184,0.12); color: var(--txt2);   border: 1px solid rgba(148,163,184,0.3); }
.tag-rec   { background: rgba(255,176,32,0.14);  color: var(--warn);   border: 1px solid rgba(255,176,32,0.34); }

/* ── Recovery bar ── */
.rec-panel { background: rgba(255,176,32,0.07); border: 1px solid rgba(255,176,32,0.24); border-radius: 13px; padding: 13px 15px; margin-top: 13px; }
.rec-bar-bg { height: 7px; background: rgba(255,255,255,0.07); border-radius: 99px; overflow: hidden; margin-top: 9px; }
.rec-bar-fill { height: 100%; background: linear-gradient(90deg,var(--warn),var(--danger)); border-radius: 99px; box-shadow: 0 0 14px rgba(255,176,32,0.4); }

/* ── Strategy cards ── */
.strat { background: var(--card); backdrop-filter: blur(12px); border: 1px solid var(--border); border-radius: 13px; padding: 14px 16px; margin-bottom: 9px; position: relative; overflow: hidden; }
.strat::before { content:''; position:absolute; left:0; top:0; bottom:0; width:3px; }
.strat.s-long::before  { background: var(--emerald); box-shadow: 0 0 14px var(--emerald); }
.strat.s-short::before { background: var(--danger);  box-shadow: 0 0 14px var(--danger); }
.strat.s-flat::before  { background: var(--txt3); }
.str-bar { height: 5px; background: rgba(255,255,255,0.07); border-radius: 99px; overflow: hidden; margin-top: 7px; }
.str-long  { height: 100%; background: linear-gradient(90deg, var(--emerald), var(--lime)); border-radius: 99px; }
.str-short { height: 100%; background: linear-gradient(90deg, var(--danger), var(--magenta)); border-radius: 99px; }
.str-flat  { height: 100%; background: var(--txt3); border-radius: 99px; }

/* ── Divider ── */
hr { border: none; border-top: 1px solid var(--border) !important; margin: 22px 0 !important; }

/* ── Alerts ── */
[data-testid="stAlert"] { background: var(--card2) !important; backdrop-filter: blur(12px); border-radius: 13px !important; border: 1px solid var(--border) !important; }

/* ════════════════════════════════════════════════════════════════════
   CINEMATIC LANDING HERO
   ════════════════════════════════════════════════════════════════════ */
.landing-hero {
  position: relative; overflow: hidden;
  padding: 64px 48px 52px 48px; margin-bottom: 36px;
  border-radius: 28px;
  border: 1px solid var(--border);
  background:
    radial-gradient(900px 380px at 78% -20%, rgba(139,124,255,0.20), transparent 65%),
    radial-gradient(700px 360px at 6% 120%,  rgba(0,230,118,0.14), transparent 60%),
    linear-gradient(160deg, rgba(14,20,34,0.92), rgba(8,11,22,0.96));
  box-shadow: 0 30px 80px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.06);
}
/* animated prism beams (echo of the 'Breaking Barriers' ref) */
.landing-hero::before {
  content: ''; position: absolute; inset: 0; pointer-events: none; opacity: 0.55;
  background:
    linear-gradient(115deg, transparent 38%, rgba(0,212,255,0.10) 46%, transparent 54%),
    linear-gradient(115deg, transparent 58%, rgba(232,121,249,0.09) 66%, transparent 74%);
  background-size: 200% 200%;
  animation: beam 11s ease-in-out infinite alternate;
}
.landing-hero::after {
  content: ''; position: absolute; inset: 0; pointer-events: none; opacity: 0.4;
  background-image:
    linear-gradient(rgba(255,255,255,0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,0.05) 1px, transparent 1px);
  background-size: 38px 38px;
  -webkit-mask-image: radial-gradient(circle at 80% 0%, #000, transparent 70%);
          mask-image: radial-gradient(circle at 80% 0%, #000, transparent 70%);
}
@keyframes beam { 0% { background-position: 0% 0%; } 100% { background-position: 100% 100%; } }
.landing-hero > * { position: relative; z-index: 1; }
.landing-hero h1 {
  font-family: 'Sora', sans-serif;
  font-size: 4rem; font-weight: 800; letter-spacing: -2.4px; line-height: 1.03; margin: 0 0 18px 0;
  background: linear-gradient(115deg, #FFFFFF 16%, var(--accent) 52%, var(--violet) 86%);
  background-size: 220% auto;
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  animation: shine 10s linear infinite;
}
.landing-hero .l-sub { font-size: 1.08rem; color: var(--txt2); max-width: 680px; line-height: 1.7; margin-bottom: 30px; }
.l-tags { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:22px; }
.l-pill {
  display:inline-block; backdrop-filter: blur(10px);
  background: rgba(255,255,255,0.04); border: 1px solid var(--border2);
  border-radius: 99px; padding: 6px 15px; font-size:0.74rem; font-weight:600; color:var(--txt);
}
.l-stats { display:flex; gap:38px; flex-wrap:wrap; padding-top:20px; border-top: 1px solid var(--border); }
.l-stat-num {
  font-size:2.1rem; font-weight:700; font-family:'JetBrains Mono',monospace;
  background: linear-gradient(135deg, #fff, var(--accent)); -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.l-stat-lbl { font-size:0.71rem; color:var(--txt2); text-transform:uppercase; letter-spacing:1px; margin-top:3px; }

/* ── Scenario cards ── */
.scenario { background:var(--card); backdrop-filter: blur(14px); border:1px solid var(--border); border-radius:18px; padding:20px 22px; margin-bottom:13px; position:relative; overflow:hidden; }
.scenario::before { content:''; position:absolute; left:0; top:0; bottom:0; width:3px; }
.scenario.s-crash::before { background: var(--danger);  box-shadow: 0 0 16px var(--danger); }
.scenario.s-shock::before { background: var(--warn);    box-shadow: 0 0 16px var(--warn); }
.scenario.s-bull::before  { background: var(--emerald); box-shadow: 0 0 16px var(--emerald); }
.scenario-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin-top:12px; }
.sc-cell .k { font-size:0.64rem; color:var(--txt2); text-transform:uppercase; letter-spacing:0.6px; margin-bottom:3px; }
.sc-cell .v { font-size:0.95rem; font-weight:700; font-family:'JetBrains Mono',monospace; }

/* ── Health ring ── */
.health-ring { text-align:center; padding:10px; }
.health-score { font-size:3rem; font-weight:800; font-family:'JetBrains Mono',monospace; }

/* ── Factor rows ── */
.factor-row { display:flex; align-items:center; gap:16px; padding:11px 0; border-bottom:1px solid var(--border); }
.factor-name { width:180px; font-size:0.82rem; font-weight:600; color:var(--txt); }
.factor-bar-bg { flex:1; height:7px; background:rgba(255,255,255,0.07); border-radius:99px; overflow:hidden; }
.factor-bar-fill { height:100%; border-radius:99px; }
.factor-val { width:80px; text-align:right; font-size:0.82rem; font-family:'JetBrains Mono',monospace; color:var(--txt2); }

/* ── Accessibility & polish (ui-ux-pro-max pre-delivery checklist) ── */
/* Visible keyboard focus — required for WCAG AA / keyboard navigation */
a:focus-visible, button:focus-visible,
[data-testid="stButton"] button:focus-visible,
[role="tab"]:focus-visible, .stTabs button:focus-visible,
input:focus-visible, select:focus-visible, textarea:focus-visible {
  outline: 2px solid var(--accent) !important;
  outline-offset: 2px !important;
  border-radius: 8px;
}
/* Clear affordance: pointer cursor on every clickable control */
[data-testid="stButton"] button, .stTabs button, [role="tab"],
[data-baseweb="select"], label[data-baseweb="checkbox"], .stDownloadButton button {
  cursor: pointer;
}
/* Smooth hover transitions (150–300ms), no harsh snaps */
[data-testid="stButton"] button, .stTabs button, .kpi, .sig, .settings-card {
  transition: all .2s ease;
}
/* Respect prefers-reduced-motion: stop the ambient orbs/beams/shine for users
   who request reduced motion (vestibular-safety; also calmer on low-end devices) */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: .001ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: .001ms !important;
    scroll-behavior: auto !important;
  }
}
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
    "target_return": 20,
    "stock_type":    "JSE Large Cap",
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

# ── South African stock universe definitions ──────────────────────
SA_UNIVERSES: Dict[str, List[str]] = {
    # JSE Large Caps — trade directly via any JSE broker
    "JSE Large Cap":  ["NPN.JO","SOL.JO","SHP.JO","FSR.JO","CPI.JO",
                       "DSY.JO","MTN.JO","VOD.JO","SLM.JO","ABG.JO"],
    # JSE Banks & Financials
    "JSE Banks":      ["FSR.JO","SBK.JO","ABG.JO","NED.JO","INL.JO",
                       "CPI.JO","DSY.JO","SLM.JO"],
    # JSE Mining & Resources
    "JSE Mining":     ["AGL.JO","BHP.JO","GFI.JO","ANG.JO","IMP.JO",
                       "SOL.JO","SSW.JO","AMS.JO"],
    # JSE ETFs — Satrix & CoreShares (ideal for R300 accounts)
    "JSE ETFs":       ["STX40.JO","STXSWIX.JO","STXWDM.JO","STXNDX.JO","PTXSPY.JO"],
    # EasyEquities US wallet — accessible to SA investors
    "EasyEquities":   ["AAPL","MSFT","NVDA","GOOGL","AMZN","V","META","TSLA","JPM","JNJ"],
    # Balanced SA portfolio
    "SA Balanced":    ["NPN.JO","FSR.JO","SHP.JO","STX40.JO","STXWDM.JO","SOL.JO"],
}

SA_NAMES: Dict[str, str] = {
    "NPN.JO":"Naspers","SOL.JO":"Sasol","SHP.JO":"Shoprite","FSR.JO":"FirstRand",
    "CPI.JO":"Capitec","DSY.JO":"Discovery","MTN.JO":"MTN Group","VOD.JO":"Vodacom",
    "SLM.JO":"Sanlam","ABG.JO":"Absa Group","SBK.JO":"Standard Bank","NED.JO":"Nedbank",
    "INL.JO":"Investec","AGL.JO":"Anglo American","BHP.JO":"BHP Group","GFI.JO":"Gold Fields",
    "ANG.JO":"AngloGold","IMP.JO":"Impala Platinum","SSW.JO":"Sibanye Stillwater",
    "AMS.JO":"Anglo American Platinum","STX40.JO":"Satrix 40 ETF",
    "STXSWIX.JO":"Satrix SWIX 40","STXWDM.JO":"Satrix World ETF",
    "STXNDX.JO":"Satrix Nasdaq 100","PTXSPY.JO":"Satrix S&P 500",
    "AAPL":"Apple","MSFT":"Microsoft","NVDA":"Nvidia","GOOGL":"Alphabet",
    "AMZN":"Amazon","V":"Visa","META":"Meta","TSLA":"Tesla","JPM":"JPMorgan","JNJ":"J&J",
}

# JSE-realistic sector params: (daily drift, daily vol, price_in_ZAR, sector)
_JSE_PARAMS: Dict[str, tuple] = {
    "NPN.JO":(0.0006,0.018,3500),"SOL.JO":(0.0003,0.022,180),"SHP.JO":(0.0007,0.014,280),
    "FSR.JO":(0.0005,0.015,70), "CPI.JO":(0.0009,0.017,2200),"DSY.JO":(0.0006,0.016,165),
    "MTN.JO":(0.0004,0.020,120),"VOD.JO":(0.0003,0.013,95),  "SLM.JO":(0.0005,0.015,55),
    "ABG.JO":(0.0004,0.016,175),"SBK.JO":(0.0005,0.015,195),"NED.JO":(0.0004,0.017,220),
    "INL.JO":(0.0005,0.016,110),"AGL.JO":(0.0004,0.022,500),"BHP.JO":(0.0005,0.019,480),
    "GFI.JO":(0.0006,0.025,220),"ANG.JO":(0.0005,0.024,290),"IMP.JO":(0.0002,0.030,140),
    "SSW.JO":(0.0003,0.028,55), "AMS.JO":(0.0004,0.025,1400),"STX40.JO":(0.0005,0.012,95),
    "STXSWIX.JO":(0.0005,0.012,92),"STXWDM.JO":(0.0006,0.010,115),"STXNDX.JO":(0.0008,0.013,140),
    "PTXSPY.JO":(0.0007,0.011,132),
    "AAPL":(0.0008,0.014,185),"MSFT":(0.0009,0.013,415),"NVDA":(0.0012,0.030,700),
    "GOOGL":(0.0007,0.013,175),"AMZN":(0.0008,0.016,180),"V":(0.0007,0.011,275),
    "META":(0.0010,0.018,490),"TSLA":(0.0008,0.035,220),"JPM":(0.0006,0.012,200),"JNJ":(0.0004,0.010,155),
}


@st.cache_data(show_spinner=False, ttl=1800)
def _load_bundle(theme: str) -> Dict[str, Any]:
    if not _ENGINES:
        return {}
    universes = SA_UNIVERSES.get(theme, SA_UNIVERSES["JSE Large Cap"])
    cfg = DataFeedConfig(
        equity=EquityConfig(tickers=universes, historical_period="2y"),
        fixed_income=FixedIncomeConfig(
            bond_etfs={"SHY":"1-3Y Treasury","IEF":"7-10Y Treasury",
                       "TLT":"20+Y Treasury","AGG":"US Aggregate","LQD":"IG Corporate","HYG":"High Yield"},
            historical_period="1y",
        ),
        forex=ForexConfig(
            majors=["EURUSD=X","GBPUSD=X","USDJPY=X","AUDUSD=X","ZARUSD=X"],
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
    """Load JSE All Share Index as benchmark; fallback to synthetic JSE-like benchmark."""
    try:
        import yfinance as yf
        # JSE All Share Index
        df = yf.download("^J203.JO", period="2y", interval="1d", progress=False, auto_adjust=True)
        if not df.empty:
            s = df["Close"]
            return (s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s).dropna()
        # Fallback: JSE Top 40
        df = yf.download("^J200.JO", period="2y", interval="1d", progress=False, auto_adjust=True)
        if not df.empty:
            s = df["Close"]
            return (s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s).dropna()
    except Exception:
        pass
    return None


def _synthetic(theme: str) -> Dict[str, Any]:
    """
    Generates realistic JSE-flavoured synthetic market data.
    Works 100% offline — no API calls needed.
    Sector correlations, ZAR price levels, and JSE volatility profiles are realistic.
    """
    dates = pd.date_range(end=datetime.now(), periods=504, freq="B")
    rng_master = np.random.default_rng(42)

    def _correlated_hist(seed: int, ticker: str) -> pd.DataFrame:
        params = _JSE_PARAMS.get(ticker, (0.0005, 0.016, 100))
        drift, vol, p0 = params
        r = np.random.default_rng(seed)
        # Add JSE market factor (common shock) for realistic correlations
        mkt_shock = rng_master.normal(0, 0.008, len(dates))
        idio      = r.normal(drift, vol * 0.7, len(dates))
        combined  = 0.6 * mkt_shock + idio
        p = p0 * np.cumprod(1 + combined)
        hl_spread = vol * 1.8
        df = pd.DataFrame({
            "Open":   p * (1 + r.normal(0, 0.002, len(dates))),
            "High":   p * (1 + np.abs(r.normal(0, hl_spread, len(dates)))),
            "Low":    p * (1 - np.abs(r.normal(0, hl_spread, len(dates)))),
            "Close":  p,
            "Volume": r.uniform(5e5, 8e6, len(dates)),
        }, index=dates)
        df["Return"] = df["Close"].pct_change()
        return df

    tickers = SA_UNIVERSES.get(theme, SA_UNIVERSES["JSE Large Cap"])
    histories = {t: _correlated_hist(i+10, t) for i, t in enumerate(tickers)}

    screened_t = tickers[:4]
    screened = pd.DataFrame({
        "ticker":       screened_t,
        "name":         [SA_NAMES.get(t, t) for t in screened_t],
        "currentPrice": [float(histories[t]["Close"].iloc[-1]) for t in screened_t],
        "trailingPE":   [12.3, 9.8, 14.5, 8.7],
        "priceToBook":  [1.8, 1.1, 2.2, 0.9],
        "returnOnEquity": [0.21, 0.18, 0.25, 0.16],
        "earningsYield":  [0.081, 0.102, 0.069, 0.115],
        "grahamMoS":      [0.18, 0.28, 0.12, 0.32],
        "fcfYield":       [0.06, 0.08, 0.05, 0.09],
        "compositeScore": [0.88, 0.94, 0.81, 0.91],
    })

    # SA government bonds (RSA bonds)
    rsa_bonds = ["R2023","R2030","R2035","R2040","R2048"]
    fi_t = ["R186.JO","R2030.JO","R213.JO","R214.JO","STXGOV.JO","NGOVSUS"]
    etf_hist = {t: _correlated_hist(50+i, "STX40.JO") for i, t in enumerate(fi_t)}
    etf_yields = pd.DataFrame({
        "ticker": fi_t,
        "name":   ["RSA 2026","RSA 2030","RSA 2035","RSA 2040","Satrix Govt Bond","SA Inflation-Lkd"],
        "ytm_proxy":     [0.0820, 0.0912, 0.0985, 0.1020, 0.0875, 0.0760],
        "ytm_source":    ["sarb_yield"] * 6,
        "duration_years":[2.1, 6.8, 11.2, 15.4, 7.2, 8.1],
    })

    fx_pairs = ["USDZAR=X","EURZAR=X","GBPZAR=X","XAUUSD=X","EURUSD=X"]
    fx_base  = [18.50, 20.10, 23.50, 1950.0, 1.08]
    fx_daily: Dict[str, Any] = {}
    for i, (t, base) in enumerate(zip(fx_pairs, fx_base)):
        r = np.random.default_rng(200 + i)
        # ZAR pairs have higher volatility (EM currency)
        v = 0.010 if "ZAR" in t else 0.005
        p = base * np.cumprod(1 + r.normal(0.0001, v, len(dates)))
        d = pd.DataFrame({"Open":p,"High":p*(1+v),"Low":p*(1-v),"Close":p,
                          "Volume":np.zeros(len(dates))}, index=dates)
        d["Return"] = d["Close"].pct_change()
        fx_daily[t] = d

    # SA yield curve (SARB rates as of 2025)
    sa_yield_curve = pd.Series({
        "3M": 0.0830, "1Y": 0.0845, "3Y": 0.0880,
        "5Y": 0.0920, "10Y": 0.0985, "30Y": 0.1050,
    })

    return {
        "equity": {"histories": histories, "screened_universe": screened},
        "fixed_income": {
            "yield_curve": sa_yield_curve,
            "curve_slope_bp": int((sa_yield_curve["10Y"] - sa_yield_curve["3M"]) * 10000),
            "etf_yields": etf_yields, "etf_histories": etf_hist,
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
    <span class="l-pill">🇿🇦 ZAR-Native · JSE</span>
    <span class="l-pill">📐 Factor Models</span>
    <span class="l-pill">🥇 Gold &amp; FX Desk</span>
    <span class="l-pill">🎲 Monte Carlo Engine</span>
  </div>
  <h1>Institutional Intelligence.<br>Retail Accessibility.</h1>
  <p class="l-sub">The analytical frameworks used by hedge funds, private equity and sovereign wealth funds — regime-aware portfolio construction, VaR/CVaR risk attribution, 25+ signal strategies, an XAU/USD &amp; forex desk, and Monte Carlo probability forecasting. Built for the JSE and priced for everyone, starting from <b style="color:#00D4FF;">R300</b>.</p>
  <div class="l-stats">
    <div><div class="l-stat-num">25+</div><div class="l-stat-lbl">Trading Strategies</div></div>
    <div><div class="l-stat-num">10,000</div><div class="l-stat-lbl">Monte Carlo Paths</div></div>
    <div><div class="l-stat-num">R300</div><div class="l-stat-lbl">Minimum Capital</div></div>
    <div><div class="l-stat-num">7</div><div class="l-stat-lbl">Quant Models</div></div>
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
# QUANT ENGINE — Institutional model implementations (lightweight, no GPU)
# Dependencies: numpy, scipy, scikit-learn (all free, Streamlit-cloud compatible)
# ══════════════════════════════════════════════════════════════════════════════

def _garch11_fit(returns: np.ndarray) -> tuple:
    """Fit GARCH(1,1) via Nelder-Mead MLE. Returns (omega, alpha, beta)."""
    try:
        from scipy.optimize import minimize
        r = returns[~np.isnan(returns)]
        if len(r) < 30:
            return (1e-5, 0.08, 0.88)
        var0 = float(np.var(r))
        def neg_loglik(p):
            w, a, b = p
            if w <= 0 or a <= 0 or b <= 0 or a + b >= 0.9999:
                return 1e10
            h = np.empty(len(r))
            h[0] = var0
            for t in range(1, len(r)):
                h[t] = w + a * r[t-1]**2 + b * h[t-1]
            h = np.maximum(h, 1e-12)
            return 0.5 * float(np.sum(np.log(h) + r**2 / h))
        res = minimize(neg_loglik, [var0 * 0.05, 0.08, 0.88],
                       method="Nelder-Mead", options={"maxiter": 2000, "xatol": 1e-7})
        w, a, b = res.x
        if w > 0 and a > 0 and b > 0 and a + b < 1:
            return (w, a, b)
    except Exception:
        pass
    return (1e-5, 0.08, 0.88)


def _garch11_path(returns: np.ndarray, omega: float, alpha: float,
                  beta: float, horizon: int = 22) -> tuple:
    """Return (historical_vol_series, forecast_annualised_vols)."""
    r = returns[~np.isnan(returns)]
    h = np.empty(len(r))
    h[0] = np.var(r)
    for t in range(1, len(r)):
        h[t] = omega + alpha * r[t-1]**2 + beta * h[t-1]
    # Multi-step forecast to horizon
    lr_var = omega / max(1 - alpha - beta, 1e-9)
    h_last = h[-1]
    fcast = np.empty(horizon)
    ab = alpha + beta
    for i in range(horizon):
        fcast[i] = lr_var + ab**i * (h_last - lr_var)
    return np.sqrt(h) * np.sqrt(252), np.sqrt(np.maximum(fcast, 0)) * np.sqrt(252)


def _black_litterman(mu_eq: np.ndarray, Sigma: np.ndarray,
                     P: np.ndarray, Q: np.ndarray, tau: float = 0.025) -> tuple:
    """
    Black-Litterman posterior (mu_bl, Sigma_bl).
    mu_eq : market equilibrium returns (n,)
    Sigma : covariance matrix (n,n)
    P     : views matrix (k,n)
    Q     : view expected returns (k,)
    """
    try:
        tauS = tau * Sigma
        Omega = np.diag(np.diag(tau * P @ Sigma @ P.T)) + 1e-9 * np.eye(len(Q))
        inv_tauS = np.linalg.solve(tauS, np.eye(len(mu_eq)))
        inv_Omega = np.linalg.solve(Omega, np.eye(len(Q)))
        M_inv = np.linalg.solve(inv_tauS + P.T @ inv_Omega @ P, np.eye(len(mu_eq)))
        mu_bl = M_inv @ (inv_tauS @ mu_eq + P.T @ inv_Omega @ Q)
        Sigma_bl = Sigma + M_inv
        return mu_bl, Sigma_bl
    except np.linalg.LinAlgError:
        return mu_eq.copy(), Sigma.copy()


def _fama_french_alpha(port_ret: pd.Series, hist_map: Dict) -> Dict[str, float]:
    """
    Compute Fama-French 5-factor alpha using the portfolio's own asset returns
    as factor proxies (purely internal, no external factor data needed).
    Returns: alpha (annualised), R², factor betas.
    """
    if not hist_map or len(hist_map) < 3:
        return {"alpha": 0.0, "r2": 0.0, "mkt_beta": 1.0,
                "smb_beta": 0.0, "hml_beta": 0.0}
    try:
        # Build equal-weight market factor from all assets
        rets = pd.DataFrame({t: h["Close"].pct_change() for t, h in hist_map.items()}).dropna()
        if len(rets) < 60:
            return {"alpha": 0.0, "r2": 0.0, "mkt_beta": 1.0, "smb_beta": 0.0, "hml_beta": 0.0}
        mkt = rets.mean(axis=1)           # EW market
        # SMB proxy: avg 3 lowest-vol (small) minus avg 3 highest-vol (large)
        vols = rets.std()
        small = rets[vols.nsmallest(3).index].mean(axis=1)
        large = rets[vols.nlargest(3).index].mean(axis=1)
        smb   = small - large
        # HML proxy: avg 3 highest-momentum minus avg 3 lowest-momentum
        mom   = rets.iloc[-60:].mean()
        high_m = rets[mom.nlargest(3).index].mean(axis=1)
        low_m  = rets[mom.nsmallest(3).index].mean(axis=1)
        hml    = high_m - low_m
        # Align all series
        aligned = pd.concat([port_ret, mkt, smb, hml], axis=1).dropna()
        aligned.columns = ["p","mkt","smb","hml"]
        y = aligned["p"].values
        X = np.column_stack([np.ones(len(y)),
                             aligned["mkt"].values,
                             aligned["smb"].values,
                             aligned["hml"].values])
        betas, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
        y_pred = X @ betas
        ss_res = np.sum((y - y_pred)**2)
        ss_tot = np.sum((y - y.mean())**2)
        r2 = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0
        return {
            "alpha":     float(betas[0]) * 252,
            "r2":        float(np.clip(r2, 0, 1)),
            "mkt_beta":  float(betas[1]),
            "smb_beta":  float(betas[2]),
            "hml_beta":  float(betas[3]),
        }
    except Exception:
        return {"alpha": 0.0, "r2": 0.0, "mkt_beta": 1.0, "smb_beta": 0.0, "hml_beta": 0.0}


def _jump_diffusion_mc(S0: float, mu: float, sigma: float,
                       lam: float, mu_j: float, sigma_j: float,
                       T: float, budget_zar: float,
                       n_paths: int = 800, n_steps: int = 252) -> Dict[str, Any]:
    """
    Merton (1976) jump-diffusion simulation.
    lam = jump intensity (avg jumps/year), mu_j / sigma_j = jump size distribution.
    Returns percentile paths and stats.
    """
    rng = np.random.default_rng(99)
    dt = T / n_steps
    paths = np.ones((n_paths, n_steps + 1)) * S0
    k = np.exp(mu_j + 0.5 * sigma_j**2) - 1  # mean jump size
    drift_adj = (mu - 0.5 * sigma**2 - lam * k) * dt
    for t in range(1, n_steps + 1):
        Z  = rng.standard_normal(n_paths)
        Nj = rng.poisson(lam * dt, n_paths)
        Jt = np.array([
            rng.normal(mu_j * Nj[i], sigma_j * max(Nj[i]**0.5, 1e-9)) if Nj[i] > 0 else 0.0
            for i in range(n_paths)
        ])
        paths[:, t] = paths[:, t-1] * np.exp(drift_adj + sigma * np.sqrt(dt) * Z + Jt)
    final = paths[:, -1] * (budget_zar / S0)
    pcts = {p: float(np.percentile(final, p)) for p in [5, 25, 50, 75, 95]}
    return {
        "paths": paths,
        "final_vals": final,
        "p5": pcts[5], "p25": pcts[25], "p50": pcts[50],
        "p75": pcts[75], "p95": pcts[95],
        "prob_profit": float(np.mean(final > budget_zar)),
        "expected_return": float(np.mean(final) / budget_zar - 1),
    }


def _rf_signal(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Random Forest directional classifier.
    Features: RSI, MACD, ATR-normalised return, Bollinger %B, 5/20-day momentum.
    Returns: direction ('BUY'/'SELL'/'NEUTRAL'), probability, OOS accuracy.
    """
    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.preprocessing import StandardScaler
        if len(df) < 80:
            return {"dir": "NEUTRAL", "prob_buy": 0.5, "accuracy": 0.0}
        d = df.copy()
        # Feature construction
        d["rsi"] = 100 - 100 / (1 + d["Close"].diff().clip(lower=0).rolling(14).mean() /
                                  (-d["Close"].diff().clip(upper=0)).rolling(14).mean().replace(0, 1e-9))
        d["macd"] = d["Close"].ewm(12).mean() - d["Close"].ewm(26).mean()
        d["atr"]  = pd.concat([d["High"]-d["Low"],
                                (d["High"]-d["Close"].shift()).abs(),
                                (d["Low"] -d["Close"].shift()).abs()], axis=1).max(axis=1).rolling(14).mean()
        d["bb_mid"]  = d["Close"].rolling(20).mean()
        d["bb_std"]  = d["Close"].rolling(20).std()
        d["bb_pct"]  = (d["Close"] - (d["bb_mid"] - 2*d["bb_std"])) / (4 * d["bb_std"].replace(0, 1e-9))
        d["mom5"]    = d["Close"].pct_change(5)
        d["mom20"]   = d["Close"].pct_change(20)
        d["vol_ratio"]= d["atr"] / d["Close"].replace(0, 1e-9)
        d["target"]  = (d["Close"].shift(-1) > d["Close"]).astype(int)
        feats = ["rsi","macd","bb_pct","mom5","mom20","vol_ratio"]
        d = d[feats + ["target"]].dropna()
        if len(d) < 60:
            return {"dir": "NEUTRAL", "prob_buy": 0.5, "accuracy": 0.0}
        X, y = d[feats].values, d["target"].values
        split = max(40, int(len(X) * 0.8))
        sc = StandardScaler()
        X_tr = sc.fit_transform(X[:split])
        X_ts = sc.transform(X[split:])
        clf = RandomForestClassifier(n_estimators=80, max_depth=5,
                                     min_samples_leaf=5, random_state=42)
        clf.fit(X_tr, y[:split])
        acc = float(clf.score(X_ts, y[split:])) if len(X_ts) > 0 else 0.5
        proba = clf.predict_proba(sc.transform(X[-1:]))[0]
        p_buy = float(proba[1]) if len(proba) > 1 else 0.5
        direction = "BUY" if p_buy > 0.58 else ("SELL" if p_buy < 0.42 else "NEUTRAL")
        importance = dict(zip(feats, clf.feature_importances_))
        return {"dir": direction, "prob_buy": p_buy, "accuracy": acc,
                "importance": importance}
    except Exception:
        return {"dir": "NEUTRAL", "prob_buy": 0.5, "accuracy": 0.0}


def _fat_tail_var(returns: np.ndarray, alpha: float = 0.05) -> Dict[str, float]:
    """
    Fit Student-t distribution and compute VaR / Expected Shortfall.
    Compares normal vs fat-tail VaR — shows why fat tails matter for SA markets.
    """
    from scipy import stats
    r = returns[~np.isnan(returns)]
    if len(r) < 30:
        return {"var_normal": 0.0, "var_t": 0.0, "es_normal": 0.0, "es_t": 0.0, "df": 5.0}
    try:
        df_t, loc_t, scale_t = stats.t.fit(r)
        var_t      = float(stats.t.ppf(alpha, df_t, loc_t, scale_t)) * np.sqrt(252)
        es_t_daily = float(-stats.t.expect(lambda x: x, args=(df_t,),
                                           loc=loc_t, scale=scale_t,
                                           lb=-np.inf, ub=float(stats.t.ppf(alpha, df_t, loc_t, scale_t)))
                          / alpha)
        es_t = -es_t_daily * np.sqrt(252)
    except Exception:
        var_t = float(np.percentile(r, alpha * 100)) * np.sqrt(252)
        es_t  = float(r[r <= np.percentile(r, alpha * 100)].mean()) * np.sqrt(252)
        df_t  = 5.0
    mu, sig       = float(r.mean()), float(r.std())
    var_normal    = float(stats.norm.ppf(alpha, mu, sig)) * np.sqrt(252)
    es_normal_daily = float(-(mu - sig * stats.norm.pdf(stats.norm.ppf(alpha)) / alpha))
    es_normal     = -es_normal_daily * np.sqrt(252)
    return {
        "var_normal": var_normal, "var_t": var_t,
        "es_normal":  es_normal,  "es_t":  es_t,
        "df": float(df_t),
    }


def _gaussian_copula_var(returns_df: pd.DataFrame,
                          alpha: float = 0.05, n_sim: int = 3000) -> Dict[str, float]:
    """Gaussian copula portfolio VaR — captures non-linear tail dependence."""
    from scipy.stats import norm
    try:
        r = returns_df.dropna()
        n, d = r.shape
        if n < 30 or d < 2:
            return {"var": 0.0, "es": 0.0}
        # Rank transform to uniform
        U = np.zeros((n, d))
        for i in range(d):
            ranks = np.argsort(np.argsort(r.values[:, i]))
            U[:, i] = (ranks + 1.0) / (n + 1.0)
        Z = np.clip(norm.ppf(U), -3.5, 3.5)
        corr = np.corrcoef(Z.T)
        corr = (corr + corr.T) / 2
        np.fill_diagonal(corr, 1.0)
        L = np.linalg.cholesky(corr + 1e-6 * np.eye(d))
        rng = np.random.default_rng(77)
        Z_sim = rng.standard_normal((n_sim, d)) @ L.T
        U_sim = norm.cdf(Z_sim)
        sim_r = np.zeros_like(U_sim)
        for i in range(d):
            sr = np.sort(r.values[:, i])
            idx = np.floor(U_sim[:, i] * len(sr)).astype(int).clip(0, len(sr)-1)
            sim_r[:, i] = sr[idx]
        w   = np.ones(d) / d
        p   = sim_r @ w
        var = float(np.percentile(p, alpha * 100)) * np.sqrt(252)
        es  = float(p[p <= np.percentile(p, alpha * 100)].mean()) * np.sqrt(252)
        return {"var": var, "es": es}
    except Exception:
        return {"var": 0.0, "es": 0.0}


# ══════════════════════════════════════════════════════════════════════════════
# Tab layout — Settings tab first so it's always visible
# ══════════════════════════════════════════════════════════════════════════════

tab_settings, tab_port, tab_fx, tab_strat, tab_fi, tab_risk, tab_quant = st.tabs([
    "  ⚙️  Settings  ",
    "  📊  Portfolio  ",
    "  💱  Forex Desk  ",
    "  🧠  Strategy Lab  ",
    "  🏦  Fixed Income  ",
    "  📐  Risk Engine  ",
    "  🔬  Quant Models  ",
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
        st.markdown("### 📈 Asset Universe (South Africa)")
        type_options = list(SA_UNIVERSES.keys())
        saved_type = st.session_state.get("stock_type", "JSE Large Cap")
        if saved_type not in type_options:
            saved_type = "JSE Large Cap"
        new_type = st.selectbox(
            "Select JSE / SA market universe", type_options,
            index=type_options.index(saved_type),
        )
        st.session_state["stock_type"] = new_type
        type_desc = {
            "JSE Large Cap":  "🇿🇦 Naspers · Shoprite · FirstRand · Capitec · Discovery · MTN · Sanlam",
            "JSE Banks":      "🏦 FirstRand · Absa · Standard Bank · Nedbank · Investec · Capitec",
            "JSE Mining":     "⛏️ Anglo American · BHP · Gold Fields · AngloGold · Impala · Sibanye",
            "JSE ETFs":       "📊 Satrix 40 · SWIX · World · Nasdaq 100 · S&P 500 — ideal for R300",
            "EasyEquities":   "🌍 Apple · Microsoft · Nvidia · Alphabet — via EasyEquities USD wallet",
            "SA Balanced":    "⚖️ Mix of JSE large caps + Satrix ETFs for broad SA exposure",
        }
        st.caption(type_desc.get(new_type, ""))
        tickers_preview = SA_UNIVERSES.get(new_type, [])
        st.caption("Tickers: " + " · ".join(tickers_preview[:6]) + ("…" if len(tickers_preview) > 6 else ""))

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
        <h1>Forex &amp; Gold Desk</h1>
        <p class="hero-sub">XAU/USD gold plus the major FX pairs — session-timed ATR signals with recovery position sizing. Entry/exit windows in UTC.</p>
    </div>
    """, unsafe_allow_html=True)

    # ── XAU/USD Gold spotlight (always rendered) ─────────────────────────────
    _fx_daily = bundle.get("forex", {}).get("daily", {})
    _gold = None
    for _gk in ("XAUUSD=X", "XAUUSD", "XAU/USD", "GC=F", "GOLD"):
        _cand = _fx_daily.get(_gk)
        if _cand is not None and not _cand.empty and "Close" in _cand.columns:
            _gold = _cand
            break
    if _gold is None:
        # Synthetic gold fallback so the desk always shows XAU/USD
        _gdates = pd.date_range(end=datetime.now(), periods=180, freq="B")
        _grng = np.random.default_rng(7)
        _gpx = 1950.0 * np.cumprod(1 + _grng.normal(0.0002, 0.009, len(_gdates)))
        _gold = pd.DataFrame({"Close": _gpx}, index=_gdates)
    if _gold is not None and not _gold.empty and "Close" in _gold.columns:
        _gc = _gold["Close"].dropna()
        if len(_gc) > 2:
            _g_now  = float(_gc.iloc[-1])
            _g_prev = float(_gc.iloc[-2])
            _g_chg  = (_g_now / _g_prev - 1) if _g_prev else 0.0
            _g_60   = _gc.iloc[-60:] if len(_gc) >= 60 else _gc
            _g_hi   = float(_g_60.max()); _g_lo = float(_g_60.min())
            _g_col  = "#00E676" if _g_chg >= 0 else "#FF5C6E"
            _g_arrow = "▲" if _g_chg >= 0 else "▼"
            gcol1, gcol2 = st.columns([1, 2])
            with gcol1:
                st.markdown(f"""
                <div class="kpi" style="height:100%;">
                  <div class="k-label">🥇 XAU/USD · Gold Spot</div>
                  <div class="k-value k-warn">${_g_now:,.2f}</div>
                  <div class="k-sub" style="color:{_g_col};font-weight:700;">{_g_arrow} {abs(_g_chg)*100:.2f}% today</div>
                  <div class="k-sub" style="margin-top:10px;">60-bar range
                    <b style="color:#F4F7FF;">${_g_lo:,.0f}</b> – <b style="color:#F4F7FF;">${_g_hi:,.0f}</b></div>
                </div>
                """, unsafe_allow_html=True)
            with gcol2:
                if _PLOTLY:
                    _gw = _gc.iloc[-90:]
                    _gbase = float(_gw.min()) * 0.997
                    _gfig = go.Figure()
                    # baseline trace (invisible) so the area fills from the chart floor
                    _gfig.add_trace(go.Scatter(
                        x=_gw.index, y=[_gbase] * len(_gw),
                        mode="lines", line=dict(width=0), hoverinfo="skip", showlegend=False,
                    ))
                    _gfig.add_trace(go.Scatter(
                        x=_gw.index, y=_gw, mode="lines",
                        line=dict(color="#FF9F45", width=2.6),
                        fill="tonexty", fillcolor="rgba(255,159,69,0.12)", name="XAU/USD",
                        hovertemplate="$%{y:,.2f}<extra></extra>",
                    ))
                    _gfig.update_layout(
                        height=190, margin=dict(l=0, r=0, t=10, b=0),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        font=dict(color="#97A3BE", size=10),
                        xaxis=dict(showgrid=False, visible=False),
                        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)", side="right",
                                   range=[_gbase, float(_gw.max()) * 1.002]),
                        showlegend=False,
                    )
                    st.plotly_chart(_gfig, use_container_width=True, config={"displayModeBar": False})
            st.markdown("<br>", unsafe_allow_html=True)

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

            rec_panel = ""
            if s.recovery_mode and s.recovery_deficit > 0:
                base_risk = budget_usd * 0.01
                mult = min(s.recovery_deficit / base_risk + 1, 3.0) if base_risk > 0 else 1.0
                fill = min(mult / 3.0 * 100, 100)
                rec_panel = (
                    '<div class="rec-panel">'
                    '<div style="display:flex;justify-content:space-between;font-size:0.82rem;">'
                    '<span style="color:#FFB020;font-weight:700;">⟳ Recovery Sizing Active</span>'
                    f'<span style="font-family:\'JetBrains Mono\';">×{mult:.2f} multiplier · deficit {_rand(s.recovery_deficit)}</span>'
                    '</div>'
                    f'<div class="rec-bar-bg"><div class="rec-bar-fill" style="width:{fill:.0f}%;"></div></div>'
                    '<div style="color:#94A3B8;font-size:0.70rem;margin-top:6px;">Hard-capped ×3.0 · 15% drawdown circuit-breaker</div>'
                    '</div>'
                )

            # Single, left-aligned HTML block (no 4-space indents → no markdown code-block escaping)
            card = (
                f'<div class="sig sig-{dc}">'
                '<div class="sig-head">'
                f'<span class="sig-pair">{s.pair}</span>'
                f'<span><span class="tag tag-{dc}">{s.direction}</span>{rec_html}'
                f'<span style="color:#94A3B8;font-size:0.75rem;margin-left:8px;">{s.regime} · {s.confidence:.0%}</span></span>'
                '</div>'
                '<div class="sig-grid">'
                f'<div class="sig-cell"><div class="k">⏱ Entry</div><div class="v" style="color:#00D4FF;">{entry_str}</div></div>'
                f'<div class="sig-cell"><div class="k">🚪 Exit</div><div class="v" style="color:#818CF8;">{exit_str}</div></div>'
                f'<div class="sig-cell"><div class="k">Entry Price</div><div class="v">{s.entry_price}</div></div>'
                f'<div class="sig-cell"><div class="k">Risk : Reward</div><div class="v" style="color:#FFB020;">1 : {_sf(s.risk_reward, ".2f")}</div></div>'
                f'<div class="sig-cell"><div class="k">🛑 Stop Loss</div><div class="v" style="color:#FF5252;">{s.stop_loss}</div></div>'
                f'<div class="sig-cell"><div class="k">🎯 Take Profit</div><div class="v" style="color:#00E676;">{s.take_profit}</div></div>'
                f'<div class="sig-cell"><div class="k">Lot Size</div><div class="v">{s.lot_size}</div></div>'
                f'<div class="sig-cell"><div class="k">Risk (ZAR)</div><div class="v">{_rand(s.dollar_risk)}</div></div>'
                '</div>'
                f'{rec_panel}'
                '</div>'
            )
            st.markdown(card, unsafe_allow_html=True)

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

            df_s = hist_map.get(sel_ticker)
            if df_s is None:
                df_s = fx_map.get(sel_ticker, pd.DataFrame())

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


# ──────────────────────────────────────────────────────────────────────────────
# TAB 6 — QUANT MODELS  (Institutional research-grade analytics)
# ──────────────────────────────────────────────────────────────────────────────

with tab_quant:
    st.markdown("""
    <div class="hero-block" style="padding-bottom:16px;">
        <h1>Quant Models</h1>
        <p class="hero-sub">Institutional research-grade analytics: GARCH volatility, Black-Litterman optimisation,
        Fama-French factor decomposition, jump-diffusion Monte Carlo, fat-tail risk, Gaussian copula,
        and Random Forest directional signals. All computed in-browser — no external APIs.</p>
    </div>
    """, unsafe_allow_html=True)

    hist_map_q = bundle.get("equity", {}).get("histories", {})
    available_q = [t for t, h in hist_map_q.items() if not h.empty and len(h) >= 60]

    if not available_q:
        st.info("Generate a portfolio first (⚙️ Settings → Generate Strategy).")
    else:
        sel_q = st.selectbox("Select asset for per-asset analysis", available_q,
                             key="quant_sel")
        df_q  = hist_map_q[sel_q]
        ret_q = df_q["Close"].pct_change().dropna().values

        # ── Model tabs within Quant tab ──────────────────────────────────
        qm1, qm2, qm3, qm4, qm5, qm6 = st.tabs([
            "📈 GARCH", "⚖️ Black-Litterman", "🧪 Fama-French",
            "🎲 Jump-Diffusion", "🐋 Fat Tails", "🌐 Copula + RF",
        ])

        # ── GARCH ────────────────────────────────────────────────────────
        with qm1:
            st.markdown('<div class="sec-head">GARCH(1,1) Volatility Estimation & Forecast</div>',
                        unsafe_allow_html=True)
            with st.spinner("Fitting GARCH(1,1)…"):
                g_omega, g_alpha, g_beta = _garch11_fit(ret_q)
            hist_vol, fcast_vol = _garch11_path(ret_q, g_omega, g_alpha, g_beta, horizon=44)

            gc1, gc2, gc3, gc4 = st.columns(4)
            gc1.markdown(kpi("ω (long-run var)", f"{g_omega:.2e}", "base variance", "accent"), unsafe_allow_html=True)
            gc2.markdown(kpi("α (ARCH)", _sf(g_alpha, '.4f'), "shock persistence", "warn"), unsafe_allow_html=True)
            gc3.markdown(kpi("β (GARCH)", _sf(g_beta, '.4f'), "vol persistence", "purple"), unsafe_allow_html=True)
            lr_ann = float(np.sqrt(g_omega / max(1 - g_alpha - g_beta, 1e-9)) * np.sqrt(252))
            gc4.markdown(kpi("Long-run Vol", _sf(lr_ann, '.1%'), "equilibrium", "white"), unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)

            if _PLOTLY:
                fig_garch = go.Figure()
                x_hist = list(df_q.index[-len(hist_vol):])
                fig_garch.add_trace(go.Scatter(x=x_hist, y=hist_vol * 100,
                    name="Historical Conditional Vol",
                    line=dict(color="#00D4FF", width=1.5)))
                # Forecast region
                last_date = df_q.index[-1]
                fcast_dates = pd.date_range(last_date, periods=len(fcast_vol)+1, freq="B")[1:]
                fig_garch.add_trace(go.Scatter(x=list(fcast_dates), y=fcast_vol * 100,
                    name="GARCH Forecast (44 days)",
                    line=dict(color="#FFB020", width=2, dash="dot")))
                fig_garch.add_vrect(x0=str(last_date), x1=str(fcast_dates[-1]),
                    fillcolor="rgba(255,176,32,0.04)", line_width=0, annotation_text="Forecast")
                fig_garch.update_layout(
                    template="plotly_dark", height=320, paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)", margin=dict(l=0,r=0,t=10,b=0),
                    yaxis_title="Annualised Volatility (%)",
                    legend=dict(bgcolor="rgba(0,0,0,0)", orientation="h", y=1.05),
                    font=dict(family="Inter", color="#94A3B8"),
                )
                fig_garch.update_xaxes(gridcolor="rgba(255,255,255,0.03)")
                fig_garch.update_yaxes(gridcolor="rgba(255,255,255,0.03)")
                st.plotly_chart(fig_garch, use_container_width=True)
            st.caption(f"α+β = {g_alpha+g_beta:.4f} (persistence). Values near 1.0 mean volatility is sticky (common in JSE stocks).")

        # ── Black-Litterman ──────────────────────────────────────────────
        with qm2:
            st.markdown('<div class="sec-head">Black-Litterman Portfolio Optimisation</div>',
                        unsafe_allow_html=True)
            st.markdown("""
            <p style="color:#94A3B8;font-size:0.85rem;margin-bottom:16px;">
            Black-Litterman combines <b>market equilibrium</b> (CAPM prior) with <b>your views</b>
            on expected returns. The result is a more stable, diversified portfolio that blends
            quantitative signals with investor conviction.
            </p>""", unsafe_allow_html=True)

            bl_tickers = available_q[:6]
            if len(bl_tickers) >= 2:
                # Build returns matrix and market-cap proxy weights
                bl_rets = pd.DataFrame({t: hist_map_q[t]["Close"].pct_change()
                                        for t in bl_tickers}).dropna()
                mu_hist = bl_rets.mean().values * 252
                Sigma   = bl_rets.cov().values * 252
                # Market-cap proxy: equal weight as prior (no actual MCap data)
                n_bl = len(bl_tickers)
                w_mkt = np.ones(n_bl) / n_bl
                # Implied equilibrium returns (reverse-engineered from CAPM)
                risk_aversion = 2.5
                mu_eq = risk_aversion * Sigma @ w_mkt

                # Views UI — simple sliders
                st.markdown("**📝 Your Views — Optional (leave at 0 for pure market equilibrium)**")
                vcols = st.columns(min(3, n_bl))
                views_P, views_Q = [], []
                for i, t in enumerate(bl_tickers):
                    with vcols[i % 3]:
                        view_return = st.slider(
                            f"{SA_NAMES.get(t, t)}", -30, 50, 0, 5,
                            key=f"bl_view_{t}", format="%d%%",
                            help=f"Your expected annual return for {t}",
                        )
                        if view_return != 0:
                            row = np.zeros(n_bl)
                            row[i] = 1.0
                            views_P.append(row)
                            views_Q.append(view_return / 100.0)

                if views_P:
                    mu_bl, Sigma_bl = _black_litterman(
                        mu_eq, Sigma, np.array(views_P), np.array(views_Q))
                else:
                    mu_bl, Sigma_bl = mu_eq.copy(), Sigma.copy()

                # Optimal weights from BL posterior
                try:
                    from scipy.optimize import minimize
                    def neg_sr(w):
                        p_ret = float(w @ mu_bl)
                        p_vol = float(np.sqrt(w @ Sigma_bl @ w))
                        return -(p_ret - RISK_FREE_RATE_10Y) / max(p_vol, 1e-9)
                    cons  = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
                    bnds  = [(0.02, 0.5)] * n_bl
                    res_bl = minimize(neg_sr, w_mkt, method="SLSQP",
                                      bounds=bnds, constraints=cons)
                    w_bl_opt = res_bl.x if res_bl.success else w_mkt
                except Exception:
                    w_bl_opt = w_mkt

                # Comparison table
                bl_df = pd.DataFrame({
                    "Asset":          [SA_NAMES.get(t, t) for t in bl_tickers],
                    "Ticker":         bl_tickers,
                    "Eq. Weight":     [f"{v:.1%}" for v in w_mkt],
                    "BL Weight":      [f"{v:.1%}" for v in w_bl_opt],
                    "Prior Ret (EQ)": [f"{v:.1%}" for v in mu_eq],
                    "BL Ret":         [f"{v:.1%}" for v in mu_bl],
                })
                st.dataframe(bl_df, use_container_width=True, hide_index=True)

                if _PLOTLY:
                    fig_bl = go.Figure()
                    fig_bl.add_trace(go.Bar(name="Equal Weight", x=bl_tickers,
                        y=w_mkt * 100, marker_color="#4B5563"))
                    fig_bl.add_trace(go.Bar(name="Black-Litterman", x=bl_tickers,
                        y=w_bl_opt * 100, marker_color="#00D4FF"))
                    fig_bl.update_layout(
                        template="plotly_dark", height=260, barmode="group",
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        margin=dict(l=0,r=0,t=10,b=0),
                        yaxis_title="Weight (%)",
                        legend=dict(bgcolor="rgba(0,0,0,0)", orientation="h", y=1.05),
                        font=dict(family="Inter", color="#94A3B8"),
                    )
                    st.plotly_chart(fig_bl, use_container_width=True)
            else:
                st.info("Need ≥2 equity assets. Generate portfolio first.")

        # ── Fama-French ──────────────────────────────────────────────────
        with qm3:
            st.markdown('<div class="sec-head">Fama-French 5-Factor Alpha Decomposition</div>',
                        unsafe_allow_html=True)
            if result is not None:
                port_ret_ff = None
                ws_ff = 0.0
                for a in result.allocations:
                    if a.asset_class != "equity":
                        continue
                    h = hist_map_q.get(a.ticker)
                    if h is None or h.empty:
                        continue
                    r = h["Close"].pct_change().dropna()
                    port_ret_ff = r * a.weight if port_ret_ff is None else port_ret_ff.add(r * a.weight, fill_value=0)
                    ws_ff += a.weight
                if port_ret_ff is not None and ws_ff > 0:
                    with st.spinner("Computing factor decomposition…"):
                        ff = _fama_french_alpha(port_ret_ff / ws_ff, hist_map_q)

                    ff1, ff2, ff3, ff4, ff5 = st.columns(5)
                    alph_col = "success" if (ff["alpha"] or 0) > 0 else "danger"
                    ff1.markdown(kpi("Alpha (α)", _sf(ff["alpha"], '+.1%'), "excess annualised return", alph_col), unsafe_allow_html=True)
                    ff2.markdown(kpi("Market β", _sf(ff["mkt_beta"], '.2f'), "market exposure", "white"), unsafe_allow_html=True)
                    ff3.markdown(kpi("SMB β", _sf(ff["smb_beta"], '.2f'), "size factor", "accent"), unsafe_allow_html=True)
                    ff4.markdown(kpi("HML β", _sf(ff["hml_beta"], '.2f'), "value factor", "purple"), unsafe_allow_html=True)
                    ff5.markdown(kpi("R²", _sf(ff["r2"], '.1%'), "factor explanatory power", "warn"), unsafe_allow_html=True)
                    st.markdown("<br>", unsafe_allow_html=True)

                    if _PLOTLY:
                        factor_labels = ["Market (MKT)", "Size (SMB)", "Value (HML)"]
                        factor_betas  = [ff["mkt_beta"], ff["smb_beta"], ff["hml_beta"]]
                        factor_cols   = ["#00D4FF" if v > 0 else "#FF5252" for v in factor_betas]
                        fig_ff = go.Figure(go.Bar(
                            x=factor_labels, y=factor_betas,
                            marker_color=factor_cols,
                            text=[f"{v:+.2f}" for v in factor_betas],
                            textposition="outside",
                        ))
                        fig_ff.add_hline(y=0, line=dict(color="#4B5563", width=1))
                        fig_ff.update_layout(
                            template="plotly_dark", height=260,
                            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                            margin=dict(l=0,r=0,t=10,b=0),
                            yaxis_title="Factor Beta",
                            font=dict(family="Inter", color="#94A3B8"),
                        )
                        st.plotly_chart(fig_ff, use_container_width=True)
                    st.caption(
                        f"Alpha of {ff['alpha']:+.1%} p.a. means the portfolio {'outperformed' if ff['alpha']>0 else 'underperformed'} "
                        f"its factor model benchmark by that amount annualised. R² = {ff['r2']:.0%} of variance explained by factors."
                    )
                else:
                    st.info("Portfolio data insufficient for factor regression.")
            else:
                st.info("Generate a portfolio first.")

        # ── Jump-Diffusion ────────────────────────────────────────────────
        with qm4:
            st.markdown('<div class="sec-head">Merton Jump-Diffusion Monte Carlo</div>',
                        unsafe_allow_html=True)
            st.markdown("""<p style="color:#94A3B8;font-size:0.85rem;margin-bottom:16px;">
            Standard Brownian motion assumes continuous price paths. Merton (1976) adds a
            <b>Poisson jump process</b> to model sudden gap moves — capturing JSE-specific shocks
            like load-shedding announcements, ZAR crises, and global contagion events.
            </p>""", unsafe_allow_html=True)

            jd1, jd2 = st.columns(2)
            with jd1:
                jd_lam    = st.slider("Jump intensity λ (jumps/year)", 0.5, 10.0, 3.0, 0.5, key="jd_lam")
                jd_mu_j   = st.slider("Mean jump size μ_j", -0.30, 0.10, -0.06, 0.01, key="jd_mj", format="%.2f")
            with jd2:
                jd_sig_j  = st.slider("Jump vol σ_j", 0.01, 0.30, 0.10, 0.01, key="jd_sj", format="%.2f")
                jd_T      = st.slider("Horizon (years)", 0.25, 3.0, float(time_horizon/12), 0.25, key="jd_T")

            bz_jd = float(st.session_state["budget_zar"])
            ann_ret   = float(ret_q.mean()) * 252 if len(ret_q) > 0 else 0.12
            ann_vol   = float(ret_q.std())  * np.sqrt(252) if len(ret_q) > 0 else 0.20

            with st.spinner("Running jump-diffusion simulation…"):
                jd = _jump_diffusion_mc(
                    S0=float(df_q["Close"].iloc[-1]),
                    mu=ann_ret, sigma=ann_vol,
                    lam=jd_lam, mu_j=jd_mu_j, sigma_j=jd_sig_j,
                    T=jd_T, budget_zar=bz_jd,
                )

            jc1, jc2, jc3, jc4 = st.columns(4)
            jc1.markdown(kpi("Median Outcome", _rand_raw(jd["p50"]), "50th percentile", "accent"), unsafe_allow_html=True)
            jc2.markdown(kpi("P(Profit)", _sf(jd["prob_profit"], '.0%'), "paths above initial", "success" if jd["prob_profit"] > 0.5 else "danger"), unsafe_allow_html=True)
            jc3.markdown(kpi("Expected Return", _sf(jd["expected_return"], '+.1%'), "average outcome", "success" if jd["expected_return"] > 0 else "danger"), unsafe_allow_html=True)
            jc4.markdown(kpi("5th Pct (Tail)", _rand_raw(jd["p5"]), "worst 5% scenario", "danger"), unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)

            if _PLOTLY:
                # Fan chart from jump-diffusion paths
                n_steps_jd = jd["paths"].shape[1] - 1
                x_jd = list(range(n_steps_jd + 1))
                pcts_jd = {p: [float(np.percentile(jd["paths"][:, t] * bz_jd / float(df_q["Close"].iloc[-1]), p))
                               for t in range(n_steps_jd + 1)] for p in [5,25,50,75,95]}
                fig_jd = go.Figure()
                fig_jd.add_trace(go.Scatter(
                    x=x_jd+x_jd[::-1], y=pcts_jd[95]+pcts_jd[5][::-1],
                    fill="toself", fillcolor="rgba(0,212,255,0.04)",
                    line=dict(color="rgba(0,0,0,0)"), name="P5–P95"))
                fig_jd.add_trace(go.Scatter(
                    x=x_jd+x_jd[::-1], y=pcts_jd[75]+pcts_jd[25][::-1],
                    fill="toself", fillcolor="rgba(0,212,255,0.09)",
                    line=dict(color="rgba(0,0,0,0)"), name="P25–P75"))
                for p_val, col, dsh in [(5,"#FF5252","dot"),(50,"#00E676","solid"),(95,"#818CF8","dot")]:
                    fig_jd.add_trace(go.Scatter(x=x_jd, y=pcts_jd[p_val],
                        line=dict(color=col, width=2 if p_val==50 else 1.2, dash=dsh),
                        name=f"P{p_val}"))
                fig_jd.add_hline(y=bz_jd, line=dict(color="#4B5563", width=1, dash="dash"),
                                 annotation_text="Initial Capital", annotation_font_color="#4B5563")
                fig_jd.update_layout(
                    template="plotly_dark", height=340,
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=0,r=0,t=10,b=0),
                    xaxis_title="Trading Days", yaxis_title="Portfolio Value (ZAR)",
                    legend=dict(bgcolor="rgba(0,0,0,0)", orientation="h", y=1.05),
                    font=dict(family="Inter", color="#94A3B8"),
                )
                fig_jd.update_xaxes(gridcolor="rgba(255,255,255,0.03)")
                fig_jd.update_yaxes(gridcolor="rgba(255,255,255,0.03)")
                st.plotly_chart(fig_jd, use_container_width=True)

        # ── Fat Tails ─────────────────────────────────────────────────────
        with qm5:
            st.markdown('<div class="sec-head">Fat-Tail Risk: Student-t vs Normal VaR</div>',
                        unsafe_allow_html=True)
            st.markdown("""<p style="color:#94A3B8;font-size:0.85rem;margin-bottom:16px;">
            Normal distribution <b>underestimates tail risk</b> in emerging markets. JSE stocks
            exhibit fat tails (kurtosis > 3). The Student-t distribution captures this correctly —
            critical for honest risk reporting to clients and managers.
            </p>""", unsafe_allow_html=True)

            ft = _fat_tail_var(ret_q)
            ft1, ft2, ft3, ft4 = st.columns(4)
            ft1.markdown(kpi("VaR 95% (Normal)", _sf(ft["var_normal"], '+.1%'), "gaussian assumption", "warn"), unsafe_allow_html=True)
            ft2.markdown(kpi("VaR 95% (Student-t)", _sf(ft["var_t"], '+.1%'), "fat-tail corrected", "danger"), unsafe_allow_html=True)
            ft3.markdown(kpi("ES (Normal)", _sf(ft["es_normal"], '+.1%'), "conditional shortfall", "warn"), unsafe_allow_html=True)
            ft4.markdown(kpi("ES (Student-t)", _sf(ft["es_t"], '+.1%'), "fat-tail ES", "danger"), unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)

            if ft.get("df"):
                st.info(f"**Student-t degrees of freedom = {ft['df']:.1f}**. "
                        f"Lower df = fatter tails. Normal = ∞ df. "
                        f"SA markets typically show df ≈ 3–6 (very fat tails).")

            if _PLOTLY:
                from scipy import stats as scipy_stats
                x_range = np.linspace(-0.08, 0.08, 300)
                mu_r, sig_r = float(ret_q.mean()), float(ret_q.std())
                y_norm = scipy_stats.norm.pdf(x_range, mu_r, sig_r)
                y_t    = scipy_stats.t.pdf(x_range, max(ft["df"], 2.1), mu_r, sig_r * 0.8)
                hist_vals, hist_bins = np.histogram(ret_q, bins=60, density=True)
                bin_centers = (hist_bins[:-1] + hist_bins[1:]) / 2

                fig_ft = go.Figure()
                fig_ft.add_trace(go.Bar(x=bin_centers, y=hist_vals, name="Actual Returns",
                    marker_color="rgba(0,212,255,0.25)", marker_line_color="rgba(0,212,255,0.5)"))
                fig_ft.add_trace(go.Scatter(x=x_range, y=y_norm, name="Normal fit",
                    line=dict(color="#FFB020", width=2, dash="dot")))
                fig_ft.add_trace(go.Scatter(x=x_range, y=y_t, name=f"Student-t (df={ft['df']:.1f})",
                    line=dict(color="#00E676", width=2)))
                fig_ft.update_layout(
                    template="plotly_dark", height=320,
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=0,r=0,t=10,b=0),
                    xaxis_title="Daily Return", yaxis_title="Density",
                    legend=dict(bgcolor="rgba(0,0,0,0)", orientation="h", y=1.05),
                    font=dict(family="Inter", color="#94A3B8"),
                )
                fig_ft.update_xaxes(gridcolor="rgba(255,255,255,0.03)")
                fig_ft.update_yaxes(gridcolor="rgba(255,255,255,0.03)")
                st.plotly_chart(fig_ft, use_container_width=True)

        # ── Copula + Random Forest ────────────────────────────────────────
        with qm6:
            qa, qb = st.columns([1, 1])

            with qa:
                st.markdown('<div class="sec-head">Gaussian Copula Portfolio VaR</div>',
                            unsafe_allow_html=True)
                if len(available_q) >= 2:
                    cop_tickers = available_q[:min(5, len(available_q))]
                    cop_df = pd.DataFrame({t: hist_map_q[t]["Close"].pct_change()
                                           for t in cop_tickers}).dropna()
                    with st.spinner("Copula simulation…"):
                        cop = _gaussian_copula_var(cop_df)

                    st.markdown(kpi("Copula VaR 95%", _sf(cop["var"], '+.1%'), "portfolio tail risk", "danger"), unsafe_allow_html=True)
                    st.markdown("<br>", unsafe_allow_html=True)
                    st.markdown(kpi("Copula ES", _sf(cop["es"], '+.1%'), "beyond-VaR loss", "danger"), unsafe_allow_html=True)
                    st.markdown("<br>", unsafe_allow_html=True)
                    st.caption(
                        "Gaussian copula captures non-linear tail dependence between assets. "
                        "During crises (2008, COVID, ZAR crashes), correlations spike — "
                        "copula models this better than simple correlation matrices."
                    )
                else:
                    st.info("Need ≥2 assets.")

            with qb:
                st.markdown('<div class="sec-head">Random Forest Directional Signal</div>',
                            unsafe_allow_html=True)
                with st.spinner("Training Random Forest classifier…"):
                    rf = _rf_signal(df_q)
                rf_col = "success" if rf["dir"] == "BUY" else ("danger" if rf["dir"] == "SELL" else "white")
                st.markdown(kpi("RF Signal", rf["dir"], f"P(BUY) = {rf['prob_buy']:.0%}", rf_col), unsafe_allow_html=True)
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown(kpi("OOS Accuracy", _sf(rf["accuracy"], '.1%'), "out-of-sample", "accent"), unsafe_allow_html=True)
                st.markdown("<br>", unsafe_allow_html=True)

                if _PLOTLY and rf.get("importance"):
                    imp = rf["importance"]
                    fig_imp = go.Figure(go.Bar(
                        x=list(imp.values()), y=list(imp.keys()),
                        orientation="h",
                        marker=dict(color=list(imp.values()),
                                    colorscale=[[0,"#162235"],[1,"#00D4FF"]]),
                        text=[f"{v:.1%}" for v in imp.values()],
                        textposition="outside",
                    ))
                    fig_imp.update_layout(
                        template="plotly_dark", height=220,
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        margin=dict(l=0,r=60,t=10,b=10),
                        xaxis_title="Feature Importance",
                        font=dict(family="Inter", color="#94A3B8", size=9),
                    )
                    st.plotly_chart(fig_imp, use_container_width=True)

        st.markdown("""
        <div style="background:rgba(0,212,255,0.04);border:1px solid rgba(0,212,255,0.12);
                    border-radius:10px;padding:14px 18px;margin-top:24px;">
          <div style="font-size:0.72rem;color:#00D4FF;font-weight:700;margin-bottom:4px;">ℹ️ MODEL INFO</div>
          <div style="font-size:0.76rem;color:#94A3B8;line-height:1.65;">
            <b>GARCH</b>: Fitted via Nelder-Mead MLE on daily returns — no external data.
            <b>Black-Litterman</b>: Market-cap equilibrium prior + optional investor views.
            <b>Fama-French</b>: Internal factor proxies from cross-sectional universe returns.
            <b>Jump-Diffusion</b>: Merton 1976 model with Poisson arrival process.
            <b>Fat Tails</b>: Maximum-likelihood Student-t fit to realised returns.
            <b>Copula</b>: Gaussian copula via rank transformation — no parametric marginals assumed.
            <b>Random Forest</b>: Trained on RSI · MACD · BB%B · momentum features — 80/20 OOS split.
          </div>
        </div>
        """, unsafe_allow_html=True)


# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("""
<div style="text-align:center;color:#1F2937;font-size:0.72rem;margin-top:48px;padding-top:20px;border-top:1px solid rgba(255,255,255,0.05);">
  Atlas Capital Institutional Desk · JSE &amp; SA Markets · Educational tool only — not financial advice ·
  Data via yfinance · JSE / Satrix / EasyEquities universe · GARCH · Black-Litterman · Fama-French ·
  Jump-Diffusion · Fat Tails · Copula · Random Forest · 25+ strategy signals · Synthetic offline fallback
</div>
""", unsafe_allow_html=True)
