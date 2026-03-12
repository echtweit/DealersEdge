"""
Signal-layer evaluator (separate from paper-trade execution).

Evaluates whether scan-time signals were directionally correct at fixed horizons
(1/3/5 trading days) based on underlying close movement.
"""
import json
from datetime import datetime

from . import db, pricing


def _signal_move_pct(signal: dict, close_px: float) -> float:
    entry = float(signal["entry_spot"])
    direction = signal.get("direction")
    if entry <= 0:
        return 0.0
    if direction == "UP":
        return (close_px - entry) / entry * 100
    if direction == "DOWN":
        return (entry - close_px) / entry * 100
    # VOL signal (straddle/strangle): absolute move
    return abs(close_px - entry) / entry * 100


def _signal_hit(signal: dict, close_px: float, move_pct: float) -> int:
    direction = signal.get("direction")
    target = signal.get("target_price")

    if direction == "UP":
        if isinstance(target, (int, float)):
            return 1 if close_px >= float(target) else 0
        return 1 if move_pct > 0 else 0
    if direction == "DOWN":
        if isinstance(target, (int, float)):
            return 1 if close_px <= float(target) else 0
        return 1 if move_pct > 0 else 0

    # VOL signal
    metadata = {}
    try:
        metadata = json.loads(signal.get("metadata_json") or "{}")
    except Exception:
        metadata = {}
    req = metadata.get("required_move_pct")
    req = float(req) if isinstance(req, (int, float)) else 2.0
    return 1 if move_pct >= req else 0


def _evaluate_signal_against_history(signal: dict, closes: list[dict]) -> dict:
    """
    Evaluate 1/3/5 trading-day outcomes using close-only data.
    Returns update dict for db.update_signal_evaluation().
    """
    if not closes:
        return {}

    entry_date = signal["entry_time"][:10]
    dates = [r["date"] for r in closes]
    try:
        start_idx = next(i for i, d in enumerate(dates) if d >= entry_date)
    except StopIteration:
        return {}

    updates = {}
    horizons = [(1, "h1"), (3, "h3"), (5, "h5")]
    for h, key in horizons:
        idx = start_idx + h
        if idx >= len(closes):
            continue
        close_px = float(closes[idx]["close"])
        move = round(_signal_move_pct(signal, close_px), 2)
        hit = _signal_hit(signal, close_px, move)
        updates[f"{key}_move_pct"] = move
        updates[f"{key}_hit"] = hit

    # status transitions
    has_h5 = updates.get("h5_move_pct") is not None or signal.get("h5_move_pct") is not None
    any_eval = bool(updates)
    if has_h5:
        updates["status"] = "EVALUATED"
    elif any_eval:
        updates["status"] = "PARTIAL"
    return updates


def evaluate_pending_signals(limit: int = 500) -> dict:
    """
    Evaluate pending/partial signals and persist outcomes.
    Returns summary dict.
    """
    db.init_db()
    with db.get_conn() as conn:
        signals = db.get_signals_for_evaluation(conn, limit=limit)
        if not signals:
            return {"processed": 0, "updated": 0}

        closes_cache = {}
        updated = 0
        for s in signals:
            ticker = s["ticker"]
            if ticker not in closes_cache:
                closes_cache[ticker] = pricing.get_daily_closes(ticker, period="2mo")
            updates = _evaluate_signal_against_history(s, closes_cache[ticker])
            if updates:
                db.update_signal_evaluation(conn, s["id"], updates)
                updated += 1
    return {"processed": len(signals), "updated": updated}


def signal_quality_report() -> str:
    db.init_db()
    with db.get_conn() as conn:
        rows = db.get_signals(conn)

    if not rows:
        return "\nNo signal-layer records yet.\n"

    total = len(rows)
    evaled = [r for r in rows if r.get("status") == "EVALUATED"]
    partial = [r for r in rows if r.get("status") == "PARTIAL"]
    pending = [r for r in rows if r.get("status") == "PENDING"]

    def hr(items, col):
        vals = [r[col] for r in items if r.get(col) is not None]
        return round(sum(vals) * 100.0 / len(vals), 1) if vals else None

    h1 = hr(evaled + partial, "h1_hit")
    h3 = hr(evaled + partial, "h3_hit")
    h5 = hr(evaled, "h5_hit")

    lines = [
        "",
        "═══ SIGNAL LAYER QUALITY ═══",
        "",
        f"  Total signals:    {total}",
        f"  Evaluated:        {len(evaled)}",
        f"  Partial:          {len(partial)}",
        f"  Pending:          {len(pending)}",
        "",
        f"  Hit rate 1D:      {f'{h1}%' if h1 is not None else '—'}",
        f"  Hit rate 3D:      {f'{h3}%' if h3 is not None else '—'}",
        f"  Hit rate 5D:      {f'{h5}%' if h5 is not None else '—'}",
        "",
    ]

    # Simple breakdown by thesis on evaluated rows.
    by_thesis = {}
    for r in evaled:
        t = r.get("thesis") or "N/A"
        by_thesis.setdefault(t, []).append(r)
    if by_thesis:
        lines.append("  By thesis (5D hit):")
        for t, items in sorted(by_thesis.items(), key=lambda kv: -len(kv[1])):
            t_h5 = hr(items, "h5_hit")
            lines.append(f"    {t}: n={len(items)} | h5={f'{t_h5}%' if t_h5 is not None else '—'}")
        lines.append("")
    return "\n".join(lines)


def signal_execution_bridge_report() -> str:
    """
    Compare signal-layer quality vs execution-layer outcomes by thesis.
    This is a cohort-level bridge (not 1:1 signal-to-trade linking).
    """
    db.init_db()
    with db.get_conn() as conn:
        sig_rows = db.get_signals(conn)
        tr_rows = db.get_all_closed_trades(conn)
        linked_rows = db.get_linked_signal_trade_rows(conn)

    if not sig_rows and not tr_rows:
        return "\nNo signal or trade records yet.\n"

    def hit_rate(rows, col):
        vals = [r[col] for r in rows if r.get(col) is not None]
        return round(sum(vals) * 100.0 / len(vals), 1) if vals else None

    by_thesis_sig = {}
    for r in sig_rows:
        t = r.get("thesis") or "N/A"
        by_thesis_sig.setdefault(t, []).append(r)

    by_thesis_tr = {}
    for r in tr_rows:
        t = r.get("thesis") or "N/A"
        by_thesis_tr.setdefault(t, []).append(r)

    all_theses = sorted(set(by_thesis_sig.keys()) | set(by_thesis_tr.keys()))
    lines = [
        "",
        "═══ SIGNAL VS EXECUTION BRIDGE ═══",
        "",
        "  Thesis                          SigN   SigH3   SigH5   TrN   TrWin   TrAvgP&L",
        "  --------------------------------------------------------------------------------",
    ]
    for t in all_theses:
        srows = by_thesis_sig.get(t, [])
        trows = by_thesis_tr.get(t, [])
        s_h3 = hit_rate(srows, "h3_hit")
        s_h5 = hit_rate(srows, "h5_hit")
        tr_win = None
        tr_avg = None
        if trows:
            pnl = [x["pnl_pct"] for x in trows if x.get("pnl_pct") is not None]
            if pnl:
                tr_win = round(sum(1 for p in pnl if p > 0) * 100.0 / len(pnl), 1)
                tr_avg = round(sum(pnl) / len(pnl), 1)
        lines.append(
            f"  {t[:30]:<30}  {len(srows):>4}  "
            f"{(str(s_h3)+'%') if s_h3 is not None else '—':>6}  "
            f"{(str(s_h5)+'%') if s_h5 is not None else '—':>6}  "
            f"{len(trows):>4}  "
            f"{(str(tr_win)+'%') if tr_win is not None else '—':>6}  "
            f"{(str(tr_avg)+'%') if tr_avg is not None else '—':>8}"
        )

    if linked_rows:
        h3_vals = [r["h3_hit"] for r in linked_rows if r.get("h3_hit") is not None]
        h5_vals = [r["h5_hit"] for r in linked_rows if r.get("h5_hit") is not None]
        pnl_vals = [r["pnl_pct"] for r in linked_rows if r.get("pnl_pct") is not None]
        win_vals = [p for p in pnl_vals if p > 0]
        lines += [
            "",
            "  Exact signal->trade linkage (closed trades with source_signal_id)",
            f"    Linked pairs: {len(linked_rows)}",
            f"    Linked signal H3 hit: {round(sum(h3_vals)*100/len(h3_vals),1) if h3_vals else '—'}%",
            f"    Linked signal H5 hit: {round(sum(h5_vals)*100/len(h5_vals),1) if h5_vals else '—'}%",
            f"    Linked trade win rate: {round(len(win_vals)*100/len(pnl_vals),1) if pnl_vals else '—'}%",
            f"    Linked trade avg P&L: {round(sum(pnl_vals)/len(pnl_vals),1) if pnl_vals else '—'}%",
        ]

    lines += [
        "",
        "  Note: Cohort section is thesis-level; linkage section uses exact source_signal_id matches.",
        "",
    ]
    return "\n".join(lines)
