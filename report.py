"""
report.py — One-click PDF report generator for the Atlas Capital desk.

Compiles a PortfolioResult, a list of ForexSignals, and the fixed-income
bundle into a single branded, presentation-ready PDF (returned as bytes so
Streamlit can offer it as a download).

Pure-Python via reportlab — no system dependencies (works on any desktop).
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
    )
    _REPORTLAB = True
except ImportError:  # pragma: no cover
    _REPORTLAB = False


# ── Brand palette (matches the dashboard) ─────────────────────────────────────
BG       = colors.HexColor("#0f1626") if _REPORTLAB else None
INK      = colors.HexColor("#1a2336") if _REPORTLAB else None
TEAL     = colors.HexColor("#2dd4bf") if _REPORTLAB else None
GOLD     = colors.HexColor("#f0b90b") if _REPORTLAB else None
GREEN    = colors.HexColor("#34d399") if _REPORTLAB else None
RED      = colors.HexColor("#f87171") if _REPORTLAB else None
GREY     = colors.HexColor("#6b7488") if _REPORTLAB else None
LIGHT    = colors.HexColor("#e8edf7") if _REPORTLAB else None
ROW_ALT  = colors.HexColor("#f4f6fb") if _REPORTLAB else None


# ── Safe formatters — never crash on None / NaN from live API ─────────────────

def _f(value: Any, spec: str, fallback: str = "—") -> str:
    """Format value with spec; return fallback if None, NaN, or error."""
    if value is None:
        return fallback
    try:
        if isinstance(value, float) and value != value:   # NaN check
            return fallback
        return format(value, spec)
    except (ValueError, TypeError):
        return fallback


def _zar_safe(usd: Any, rate: float, fallback: str = "—") -> str:
    if usd is None:
        return fallback
    try:
        return f"R{float(usd) * rate:,.2f}"
    except (ValueError, TypeError):
        return fallback


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("AtlasTitle", parent=ss["Title"],
                          fontSize=24, textColor=colors.HexColor("#0f1626"),
                          spaceAfter=2, leading=28))
    ss.add(ParagraphStyle("AtlasSub", parent=ss["Normal"],
                          fontSize=10, textColor=colors.HexColor("#6b7488"),
                          spaceAfter=10))
    ss.add(ParagraphStyle("AtlasH2", parent=ss["Heading2"],
                          fontSize=13, textColor=colors.HexColor("#1ba8a0"),
                          spaceBefore=14, spaceAfter=6))
    ss.add(ParagraphStyle("AtlasBody", parent=ss["Normal"],
                          fontSize=9.5, textColor=colors.HexColor("#1a2336"),
                          leading=14))
    ss.add(ParagraphStyle("AtlasNote", parent=ss["Normal"],
                          fontSize=8, textColor=colors.HexColor("#6b7488"),
                          leading=11))
    ss.add(ParagraphStyle("Banner", parent=ss["Normal"],
                          fontSize=13, textColor=colors.white, leading=18,
                          alignment=1))
    return ss


def _zar(usd: float, rate: float) -> str:
    return f"R{usd * rate:,.2f}"


def _metric_table(rows: List[List[str]], col_widths=None) -> Table:
    """A 4-up KPI card row."""
    t = Table([rows], colWidths=col_widths or [44 * mm] * len(rows))
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), INK),
        ("TEXTCOLOR", (0, 0), (-1, -1), LIGHT),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("INNERGRID", (0, 0), (-1, -1), 6, colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def _data_table(header: List[str], rows: List[List[str]],
                col_widths=None) -> Table:
    data = [header] + rows
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d8deea")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    t.setStyle(TableStyle(style))
    return t


def build_pdf_report(
    result:      Any,                    # PortfolioResult
    signals:     List[Any],              # List[ForexSignal]
    fi_bundle:   Dict[str, Any],         # bundle["fixed_income"]
    budget_zar:  float,
    usd_zar:     float,
    forex_wf:    Optional[Dict[str, Any]] = None,
) -> bytes:
    """Render the full desk report and return PDF bytes."""
    if not _REPORTLAB:
        raise RuntimeError("reportlab not installed — run: pip install reportlab")

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title="Atlas Capital — Strategy Report",
    )
    ss = _styles()
    story: List[Any] = []

    # ── Header ────────────────────────────────────────────────────────────
    story.append(Paragraph("ATLAS CAPITAL · Strategy Report", ss["AtlasTitle"]))
    budget_usd = budget_zar / max(usd_zar, 0.01)
    story.append(Paragraph(
        f"Generated {datetime.now():%Y-%m-%d %H:%M} · "
        f"Budget R{budget_zar:,.2f} (approx ${budget_usd:,.2f}) · "
        f"USD/ZAR {usd_zar:.2f}",
        ss["AtlasSub"]))
    story.append(HRFlowable(width="100%", color=TEAL, thickness=1.5))
    story.append(Spacer(1, 8))

    # ── Probability banner ────────────────────────────────────────────────
    if result is not None:
        req = result.request
        prob_str   = _f(result.monte_carlo_prob,   ".0%", "—")
        tgt_str    = _f(getattr(req, "target_return", None), ".0%", "—")
        med_str    = _f(result.mc_median_return,   "+.1%", "—")
        p10_str    = _f(result.mc_p10,             "+.1%", "—")
        p90_str    = _f(result.mc_p90,             "+.1%", "—")
        n_sims     = getattr(req, "monte_carlo_sims", 10_000)
        horizon    = getattr(req, "time_horizon_months", "?")

        banner = Table([[Paragraph(
            f"<b>Hold for {horizon} months  →  {prob_str} likelihood of a "
            f"{tgt_str}+ gain</b><br/>"
            f"<font size=9 color='#cfe9e4'>Median {med_str} · "
            f"downside P10 {p10_str} · upside P90 {p90_str} · "
            f"{n_sims:,} Monte-Carlo paths</font>",
            ss["Banner"])]], colWidths=[178 * mm])
        banner.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#13302c")),
            ("TOPPADDING",    (0, 0), (-1, -1), 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ("LEFTPADDING",   (0, 0), (-1, -1), 16),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 16),
            ("LINEBELOW",     (0, 0), (-1, -1), 2, GOLD),
        ]))
        story.append(banner)
        story.append(Spacer(1, 12))

        # ── KPI cards ─────────────────────────────────────────────────────
        regime_lbl  = f"{result.regime} ({_f(result.regime_confidence, '.0%')})"
        ret_lbl     = _f(result.expected_return, ".1%")
        sharpe_lbl  = _f(result.sharpe_ratio, ".2f")
        eq_lbl      = _f(result.equity_weight, ".0%")
        fi_lbl      = _f(result.fi_weight,     ".0%")
        story.append(_metric_table([
            f"REGIME\n{regime_lbl}",
            f"EXPECTED RETURN\n{ret_lbl} p.a.",
            f"SHARPE\n{sharpe_lbl}",
            f"EQUITY / FI\n{eq_lbl} / {fi_lbl}",
        ]))
        story.append(Spacer(1, 14))

        # ── Portfolio allocation ──────────────────────────────────────────
        story.append(Paragraph("Recommended Portfolio", ss["AtlasH2"]))
        rows = []
        for a in result.allocations:
            rows.append([
                str(a.ticker),
                "Equity" if a.asset_class == "equity" else "Fixed Income",
                _f(a.weight,        ".1%"),
                _zar_safe(a.dollar_amount, usd_zar),
                _f(a.price,         ",.2f", "—"),
                _f(a.shares,        ".4f",  "—"),
                str(a.rationale)[:42],
            ])
        story.append(_data_table(
            ["Ticker", "Class", "Weight", "Allocation (ZAR)", "Price ($)", "Units", "Rationale"],
            rows,
            col_widths=[18*mm, 22*mm, 16*mm, 26*mm, 20*mm, 18*mm, 58*mm],
        ))
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            f"Total invested: {_zar_safe(result.total_invested, usd_zar)} of "
            f"R{budget_zar:,.2f} budget · "
            f"Expected volatility {_f(result.expected_volatility, '.1%')} p.a.",
            ss["AtlasNote"]))

        # ── Walk-forward ──────────────────────────────────────────────────
        if result.walk_forward and result.walk_forward.windows > 0:
            wf = result.walk_forward
            story.append(Paragraph("Walk-Forward Validation (out-of-sample)", ss["AtlasH2"]))
            story.append(_data_table(
                ["Windows", "Win Rate", "OOS Sharpe", "Max Drawdown"],
                [[str(wf.windows),
                  _f(wf.win_rate,           ".0%"),
                  _f(wf.mean_oos_sharpe,    ".2f"),
                  _f(wf.max_drawdown,       ".1%")]],
                col_widths=[44*mm]*4,
            ))

    # ── Forex signals ─────────────────────────────────────────────────────
    if signals:
        story.append(Paragraph("Forex Signal Feed", ss["AtlasH2"]))
        rows = []
        for s in sorted(signals, key=lambda x: x.confidence, reverse=True):
            ew = getattr(s, "entry_window_utc", (0, 0))
            xw = getattr(s, "exit_window_utc",  (0, 0))
            ent = f"{ew[0]:02d}-{ew[1]:02d}h"
            ext = f"{xw[0]:02d}-{xw[1]:02d}h"
            rows.append([
                str(s.pair),
                s.direction + (" (R)" if getattr(s, "recovery_mode", False) else ""),
                ent, ext,
                _f(s.stop_loss,    "g",   str(s.stop_loss)),
                _f(s.take_profit,  "g",   str(s.take_profit)),
                f"1:{_f(s.risk_reward, '.2f')}",
                _f(s.lot_size,     "g",   "—"),
                _f(s.confidence,   ".0%", "—"),
            ])
        story.append(_data_table(
            ["Pair", "Dir", "Entry(UTC)", "Exit(UTC)", "Stop", "Target", "RR", "Lot", "Conf"],
            rows,
            col_widths=[22*mm, 16*mm, 20*mm, 20*mm, 22*mm, 22*mm, 14*mm, 16*mm, 14*mm],
        ))
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            "(R) = Recovery sizing active (next win recovers prior deficit + base "
            "profit, capped at x3.0 with a 15% drawdown circuit-breaker).",
            ss["AtlasNote"]))

    # ── Forex walk-forward ────────────────────────────────────────────────
    if forex_wf:
        story.append(Paragraph("Forex Walk-Forward Backtest", ss["AtlasH2"]))
        rows = []
        for _, r in forex_wf.items():
            rows.append([
                str(r.pair),
                str(r.total_trades),
                _f(r.win_rate,      ".0%"),
                _zar_safe(r.total_pnl_usd, usd_zar),
                _f(r.profit_factor, ".2f"),
                _f(r.sharpe,        ".2f"),
            ])
        if rows:
            story.append(_data_table(
                ["Pair", "Trades", "Win Rate", "Net PnL", "Profit Factor", "Sharpe"],
                rows, col_widths=[26*mm, 22*mm, 26*mm, 30*mm, 36*mm, 24*mm]))

    # ── Fixed income ──────────────────────────────────────────────────────
    etf = fi_bundle.get("etf_yields") if fi_bundle else None
    if etf is not None and not etf.empty and "ytm_proxy" in etf.columns:
        story.append(Paragraph("Fixed-Income Yields (YTM)", ss["AtlasH2"]))
        valid = etf.dropna(subset=["ytm_proxy"]).sort_values("ytm_proxy", ascending=False)
        rows = []
        for _, r in valid.iterrows():
            dur = r.get("duration_years")
            rows.append([
                str(r["ticker"]),
                str(r.get("name", "—"))[:34],
                _f(r["ytm_proxy"], ".2%"),
                _f(dur, ".1f") if dur is not None else "—",
            ])
        story.append(_data_table(
            ["Ticker", "Fund", "YTM", "Duration (yrs)"], rows,
            col_widths=[22*mm, 80*mm, 30*mm, 36*mm]))

        slope = fi_bundle.get("curve_slope_bp")
        slope_str = _f(slope, "+.0f")
        if slope is not None and slope_str != "—":
            try:
                tag = "INVERTED — recession signal" if float(slope) < 0 else "normal upward slope"
            except (TypeError, ValueError):
                tag = "—"
            story.append(Spacer(1, 4))
            story.append(Paragraph(
                f"US Treasury 10Y-3M spread: {slope_str} bp ({tag}).",
                ss["AtlasNote"]))

    # ── Footer / disclaimer ───────────────────────────────────────────────
    story.append(Spacer(1, 18))
    story.append(HRFlowable(width="100%", color=GREY, thickness=0.6))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "<b>Disclaimer.</b> Atlas Capital is an educational decision-support "
        "tool, not licensed financial advice. Market data via yfinance. Live "
        "forex execution should be routed to an MQL5 Expert Advisor (MetaTrader 5). "
        "Past and simulated performance does not guarantee future results.",
        ss["AtlasNote"]))

    doc.build(story)
    pdf = buf.getvalue()
    buf.close()
    return pdf
