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
    story.append(Paragraph(
        f"Generated {datetime.now():%Y-%m-%d %H:%M} · "
        f"Budget {_zar(budget_zar / usd_zar, usd_zar)} "
        f"(≈ ${budget_zar / usd_zar:,.2f}) · USD/ZAR {usd_zar:.2f}",
        ss["AtlasSub"]))
    story.append(HRFlowable(width="100%", color=TEAL, thickness=1.5))
    story.append(Spacer(1, 8))

    # ── Probability banner ────────────────────────────────────────────────
    if result is not None:
        req = result.request
        banner = Table([[Paragraph(
            f"<b>Hold for {req.time_horizon_months} months → "
            f"{result.monte_carlo_prob:.0%} likelihood of a "
            f"{req.target_return:.0%}+ gain</b><br/>"
            f"<font size=9 color='#cfe9e4'>Median {result.mc_median_return:+.1%} · "
            f"downside (P10) {result.mc_p10:+.1%} · upside (P90) {result.mc_p90:+.1%} · "
            f"{req.monte_carlo_sims:,} Monte-Carlo paths</font>",
            ss["Banner"])]], colWidths=[178 * mm])
        banner.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#13302c")),
            ("TOPPADDING", (0, 0), (-1, -1), 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ("LEFTPADDING", (0, 0), (-1, -1), 16),
            ("RIGHTPADDING", (0, 0), (-1, -1), 16),
            ("LINEBELOW", (0, 0), (-1, -1), 2, GOLD),
        ]))
        story.append(banner)
        story.append(Spacer(1, 12))

        # ── KPI cards ─────────────────────────────────────────────────────
        story.append(_metric_table([
            f"REGIME\n{result.regime} ({result.regime_confidence:.0%})",
            f"EXPECTED RETURN\n{result.expected_return:.1%} p.a.",
            f"SHARPE\n{result.sharpe_ratio:.2f}",
            f"EQUITY / FI\n{result.equity_weight:.0%} / {result.fi_weight:.0%}",
        ]))
        story.append(Spacer(1, 14))

        # ── Portfolio allocation ──────────────────────────────────────────
        story.append(Paragraph("Recommended Portfolio", ss["AtlasH2"]))
        rows = []
        for a in result.allocations:
            rows.append([
                a.ticker,
                "Equity" if a.asset_class == "equity" else "Fixed Income",
                f"{a.weight:.1%}",
                _zar(a.dollar_amount, usd_zar),
                f"${a.price:,.2f}",
                f"{a.shares:.4f}",
                a.rationale[:42],
            ])
        story.append(_data_table(
            ["Ticker", "Class", "Weight", "Allocation", "Price", "Units", "Rationale"],
            rows,
            col_widths=[18*mm, 22*mm, 16*mm, 26*mm, 20*mm, 18*mm, 58*mm],
        ))
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            f"Total invested: {_zar(result.total_invested, usd_zar)} of "
            f"{_zar(budget_zar / usd_zar, usd_zar)} budget · "
            f"Expected volatility {result.expected_volatility:.1%} p.a.",
            ss["AtlasNote"]))

        # ── Walk-forward ──────────────────────────────────────────────────
        if result.walk_forward and result.walk_forward.windows > 0:
            wf = result.walk_forward
            story.append(Paragraph("Walk-Forward Validation (out-of-sample)", ss["AtlasH2"]))
            story.append(_data_table(
                ["Windows", "Win Rate", "OOS Sharpe", "Max Drawdown"],
                [[str(wf.windows), f"{wf.win_rate:.0%}",
                  f"{wf.mean_oos_sharpe:.2f}", f"{wf.max_drawdown:.1%}"]],
                col_widths=[44*mm]*4,
            ))

    # ── Forex signals ─────────────────────────────────────────────────────
    if signals:
        story.append(Paragraph("Forex Signal Feed", ss["AtlasH2"]))
        rows = []
        for s in sorted(signals, key=lambda x: x.confidence, reverse=True):
            ent = f"{s.entry_window_utc[0]:02d}-{s.entry_window_utc[1]:02d}h"
            ext = f"{s.exit_window_utc[0]:02d}-{s.exit_window_utc[1]:02d}h"
            rows.append([
                s.pair,
                s.direction + (" (R)" if s.recovery_mode else ""),
                ent, ext,
                f"{s.stop_loss}", f"{s.take_profit}",
                f"1:{s.risk_reward:.2f}",
                f"{s.lot_size}",
                f"{s.confidence:.0%}",
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
                r.pair, str(r.total_trades), f"{r.win_rate:.0%}",
                _zar(r.total_pnl_usd, usd_zar), f"{r.profit_factor:.2f}",
                f"{r.sharpe:.2f}",
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
            rows.append([
                str(r["ticker"]),
                str(r.get("name", "—"))[:34],
                f"{r['ytm_proxy']:.2%}",
                f"{r.get('duration_years', float('nan')):.1f}"
                if r.get("duration_years") == r.get("duration_years") else "—",
            ])
        story.append(_data_table(
            ["Ticker", "Fund", "YTM", "Duration (yrs)"], rows,
            col_widths=[22*mm, 80*mm, 30*mm, 36*mm]))

        slope = fi_bundle.get("curve_slope_bp")
        if slope is not None:
            tag = "INVERTED — recession signal" if slope < 0 else "normal upward slope"
            story.append(Spacer(1, 4))
            story.append(Paragraph(
                f"US Treasury 10Y–3M spread: {slope:+.0f} bp ({tag}).",
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
