"""
Reporter: performance analytics and signal attribution from closed trades.
"""
import math
from collections import defaultdict
from datetime import datetime
from typing import Optional

from . import db


# ── formatting helpers ───────────────────────────────────────────

def _pct(v: Optional[float]) -> str:
    return f"{v:+.1f}%" if v is not None else "—"


def _usd(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"${v:,.0f}" if abs(v) >= 1 else f"${v:,.2f}"


def _bar(val: float, width: int = 20) -> str:
    filled = max(0, min(width, int(val / 100 * width)))
    return "█" * filled + "░" * (width - filled)


def _table(headers: list[str], rows: list[list[str]], col_widths: Optional[list[int]] = None):
    """Render a simple ASCII table."""
    if not col_widths:
        col_widths = []
        for i, h in enumerate(headers):
            max_w = len(h)
            for row in rows:
                if i < len(row):
                    max_w = max(max_w, len(str(row[i])))
            col_widths.append(min(max_w + 2, 40))

    def fmt_row(cells):
        return "  ".join(str(c).ljust(w) for c, w in zip(cells, col_widths))

    lines = [fmt_row(headers), "─" * sum(col_widths + [2 * (len(headers) - 1)])]
    for row in rows:
        lines.append(fmt_row(row))
    return "\n".join(lines)


# ── metric computations ─────────────────────────────────────────

def _compute_stats(trades: list[dict]) -> dict:
    if not trades:
        return {"count": 0}

    pnls = [t["pnl_pct"] for t in trades if t.get("pnl_pct") is not None]
    pnl_dollars = [t["pnl_dollars"] for t in trades if t.get("pnl_dollars") is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    hold_days = []
    for t in trades:
        if t.get("entry_time") and t.get("exit_time"):
            entry = datetime.fromisoformat(t["entry_time"])
            exit_ = datetime.fromisoformat(t["exit_time"])
            hold_days.append((exit_ - entry).days)

    avg_pnl = sum(pnls) / len(pnls) if pnls else 0
    std_pnl = (sum((p - avg_pnl) ** 2 for p in pnls) / len(pnls)) ** 0.5 if len(pnls) > 1 else 0
    sharpe = avg_pnl / std_pnl if std_pnl > 0 else 0

    cumulative = 0
    peak = 0
    max_dd = 0
    for p in pnl_dollars:
        cumulative += p
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)

    return {
        "count": len(trades),
        "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
        "avg_pnl_pct": round(avg_pnl, 2),
        "total_pnl": round(sum(pnl_dollars), 2) if pnl_dollars else 0,
        "best": round(max(pnls), 2) if pnls else 0,
        "worst": round(min(pnls), 2) if pnls else 0,
        "sharpe": round(sharpe, 2),
        "max_drawdown": round(max_dd, 2),
        "avg_hold_days": round(sum(hold_days) / len(hold_days), 1) if hold_days else 0,
        "exits": _count_exits(trades),
    }


def _count_exits(trades: list[dict]) -> dict:
    counts = defaultdict(int)
    for t in trades:
        reason = t.get("exit_reason", "UNKNOWN")
        counts[reason] += 1
    return dict(counts)


# ── public reports ───────────────────────────────────────────────

def overall_report() -> str:
    db.init_db()
    with db.get_conn() as conn:
        trades = db.get_all_closed_trades(conn)
        open_trades = db.get_open_trades(conn)
        iv_rows = conn.execute(
            """
            SELECT t.id AS trade_id, pc.iv_confirmation
            FROM trades t
            LEFT JOIN (
                SELECT p1.trade_id, p1.iv_confirmation
                FROM price_checks p1
                JOIN (
                    SELECT trade_id, MAX(id) AS max_id
                    FROM price_checks
                    GROUP BY trade_id
                ) p2 ON p2.max_id = p1.id
            ) pc ON pc.trade_id = t.id
            WHERE t.status = 'CLOSED'
            """
        ).fetchall()

    if not trades and not open_trades:
        return "No trades recorded yet."

    lines = ["", "═══ OVERALL PERFORMANCE ═══", ""]

    if trades:
        s = _compute_stats(trades)
        lines.append(f"  Closed trades:  {s['count']}")
        lines.append(f"  Win rate:       {s['win_rate']}% {_bar(s['win_rate'])}")
        lines.append(f"  Avg P&L:        {_pct(s['avg_pnl_pct'])}")
        lines.append(f"  Total P&L:      {_usd(s['total_pnl'])}")
        lines.append(f"  Best / Worst:   {_pct(s['best'])} / {_pct(s['worst'])}")
        lines.append(f"  Sharpe:         {s['sharpe']}")
        lines.append(f"  Max Drawdown:   {_usd(s['max_drawdown'])}")
        lines.append(f"  Avg Hold:       {s['avg_hold_days']} days")
        exits = s["exits"]
        exit_str = ", ".join(f"{k}: {v}" for k, v in sorted(exits.items()))
        lines.append(f"  Exit reasons:   {exit_str}")
    else:
        lines.append("  No closed trades yet.")

    lines.append(f"\n  Open positions: {len(open_trades)}")

    if trades:
        iv_by_trade = {r["trade_id"]: r["iv_confirmation"] for r in iv_rows}
        confirmed = [t for t in trades if iv_by_trade.get(t["id"]) == 1]
        unconfirmed = [t for t in trades if iv_by_trade.get(t["id"]) != 1]
        if confirmed and unconfirmed:
            c = _compute_stats(confirmed)
            u = _compute_stats(unconfirmed)
            lines.append("\n  IV Confirmation Uplift (latest check state)")
            lines.append(
                f"    Confirmed:   n={c['count']} | win={c['win_rate']}% | avg={_pct(c['avg_pnl_pct'])}"
            )
            lines.append(
                f"    Unconfirmed: n={u['count']} | win={u['win_rate']}% | avg={_pct(u['avg_pnl_pct'])}"
            )

    lines.append("")
    return "\n".join(lines)


def signal_attribution() -> str:
    """Break down performance by signal dimensions."""
    db.init_db()
    with db.get_conn() as conn:
        trades = db.get_all_closed_trades(conn)

    if not trades:
        return "No closed trades for attribution analysis."

    dimensions = [
        ("Thesis", "thesis"),
        ("Confidence", "confidence"),
        ("Edge Type", "edge_type"),
        ("GEX Regime", "gex_regime"),
        ("Reynolds Regime", "reynolds_regime"),
        ("ACF Regime", "acf_regime"),
        ("Entropy Regime", "entropy_regime"),
        ("VRP Label", "vrp_label"),
    ]

    lines = ["", "═══ SIGNAL ATTRIBUTION ═══"]

    for dim_name, dim_key in dimensions:
        groups = defaultdict(list)
        for t in trades:
            val = t.get(dim_key) or "N/A"
            groups[val].append(t)

        if len(groups) <= 1 and "N/A" in groups:
            continue

        lines.append(f"\n  ── {dim_name} ──")
        headers = ["Value", "Trades", "Win%", "Avg P&L", "Total $"]
        rows = []
        for val, group in sorted(groups.items(), key=lambda x: -len(x[1])):
            s = _compute_stats(group)
            rows.append([
                val,
                str(s["count"]),
                f"{s['win_rate']}%",
                _pct(s["avg_pnl_pct"]),
                _usd(s["total_pnl"]),
            ])
        lines.append(_table(headers, rows))

    # Kelly accuracy: correlation between kelly_pct and actual pnl_pct
    kelly_trades = [(t["kelly_pct"], t["pnl_pct"]) for t in trades
                    if t.get("kelly_pct") and t.get("pnl_pct") is not None]
    if len(kelly_trades) >= 5:
        ks, ps = zip(*kelly_trades)
        n = len(ks)
        mean_k = sum(ks) / n
        mean_p = sum(ps) / n
        cov = sum((k - mean_k) * (p - mean_p) for k, p in zip(ks, ps)) / n
        std_k = (sum((k - mean_k) ** 2 for k in ks) / n) ** 0.5
        std_p = (sum((p - mean_p) ** 2 for p in ps) / n) ** 0.5
        corr = cov / (std_k * std_p) if std_k > 0 and std_p > 0 else 0
        lines.append(f"\n  ── Kelly Accuracy ──")
        lines.append(f"  Kelly % vs Actual P&L correlation: {corr:+.3f} (n={n})")
        assessment = "strong" if abs(corr) > 0.5 else "moderate" if abs(corr) > 0.2 else "weak"
        lines.append(f"  Assessment: {assessment} {'positive' if corr > 0 else 'negative'} relationship")

    lines.append("")
    return "\n".join(lines)


def open_positions_report() -> str:
    """Show all open trades with unrealized P&L."""
    db.init_db()
    with db.get_conn() as conn:
        trades = db.get_open_trades(conn)
        latest_checks = conn.execute(
            """
            SELECT pc.trade_id, pc.check_time, pc.unrealized_pnl_pct
            FROM price_checks pc
            JOIN (
                SELECT trade_id, MAX(id) AS max_id
                FROM price_checks
                GROUP BY trade_id
            ) latest ON latest.max_id = pc.id
            """
        ).fetchall()

    if not trades:
        return "\nNo open positions.\n"

    latest_by_trade = {row["trade_id"]: dict(row) for row in latest_checks}

    headers = [
        "ID", "Ticker", "Type", "Strike", "Expiry",
        "Entry$", "U-PnL%", "LastChk", "Days", "Thesis"
    ]
    rows = []
    for t in trades:
        days = 0
        if t.get("entry_time"):
            entry = datetime.fromisoformat(t["entry_time"])
            days = (datetime.utcnow() - entry).days
        latest = latest_by_trade.get(t["id"])
        u_pnl = _pct(latest.get("unrealized_pnl_pct")) if latest else "—"
        chk = latest.get("check_time", "—") if latest else "—"
        if chk != "—":
            try:
                chk = datetime.fromisoformat(chk).strftime("%m-%d %H:%M")
            except ValueError:
                pass

        rows.append([
            str(t["id"]),
            t["ticker"],
            t.get("option_type", "?"),
            f"${t['strike']:.0f}" if t.get("strike") else "—",
            t.get("expiry_date", "—"),
            f"${t['entry_premium']:.2f}" if t.get("entry_premium") else "—",
            u_pnl,
            chk,
            str(days),
            t.get("thesis", "—"),
        ])

    return f"\n═══ OPEN POSITIONS ({len(trades)}) ═══\n\n" + _table(headers, rows) + "\n"


def trade_history(limit: int = 50) -> str:
    """Show recent closed trades."""
    db.init_db()
    with db.get_conn() as conn:
        trades = db.get_closed_trades(conn, limit)

    if not trades:
        return "\nNo closed trades yet.\n"

    headers = ["ID", "Ticker", "Type", "Strike", "P&L%", "P&L$", "Exit", "Held"]
    rows = []
    for t in trades:
        days = 0
        if t.get("entry_time") and t.get("exit_time"):
            entry = datetime.fromisoformat(t["entry_time"])
            exit_ = datetime.fromisoformat(t["exit_time"])
            days = (exit_ - entry).days

        rows.append([
            str(t["id"]),
            t["ticker"],
            t.get("option_type", "?"),
            f"${t['strike']:.0f}" if t.get("strike") else "—",
            _pct(t.get("pnl_pct")),
            _usd(t.get("pnl_dollars")),
            t.get("exit_reason", "—"),
            f"{days}d",
        ])

    return f"\n═══ TRADE HISTORY (last {limit}) ═══\n\n" + _table(headers, rows) + "\n"
