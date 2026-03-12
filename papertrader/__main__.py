"""
CLI entry point for PaperTrader.

Usage:
    python -m papertrader scan [TICKERS...]      Scan for new signals and open trades
    python -m papertrader check                  Check open positions for exits
    python -m papertrader status                 Show open positions
    python -m papertrader report                 Overall performance summary
    python -m papertrader analyze                Signal attribution breakdown
    python -m papertrader eval-signals           Evaluate signal-layer outcomes (1/3/5D)
    python -m papertrader signal-report          Signal-layer quality report
    python -m papertrader compare-layers         Compare signal quality vs execution results
    python -m papertrader history [--limit N]    Closed trade log
    python -m papertrader health                 System health check
    python -m papertrader cron                   Print crontab snippet
    python -m papertrader ensure-backend         Ensure backend is running
"""
import argparse
import logging
import sys

from . import db
from .backend_guard import ensure_backend_available
from .config import (
    DEFAULT_WATCHLIST,
    DEFAULT_ACCOUNT_SIZE,
    CRON_TEMPLATE,
    CHALLENGER_V1_ENABLED,
    CHALLENGER_V2_ENABLED,
    CHALLENGER_V3_ENABLED,
)
from .scanner import scan_watchlist
from .monitor import check_all_open, check_all_open_with_meta
from .reporter import (
    overall_report,
    signal_attribution,
    open_positions_report,
    trade_history,
)
from .signal_evaluator import (
    evaluate_pending_signals,
    signal_quality_report,
    signal_execution_bridge_report,
)


def cmd_scan(args):
    ok, msg = ensure_backend_available()
    if ok:
        print(f"Backend guard: {msg}")
    else:
        print(f"Backend guard: {msg}")
        sys.exit(1)

    tickers = args.tickers if args.tickers else DEFAULT_WATCHLIST
    account_size = args.account_size or DEFAULT_ACCOUNT_SIZE
    print(f"Scanning {len(tickers)} tickers: {', '.join(tickers)}")
    if CHALLENGER_V3_ENABLED:
        profile = "challenger_v3"
    elif CHALLENGER_V2_ENABLED:
        profile = "challenger_v2"
    elif CHALLENGER_V1_ENABLED:
        profile = "challenger_v1"
    else:
        profile = "baseline_v1"
    print(f"Profile: {profile}")
    if account_size:
        print(f"Account size: ${account_size:,.0f}")
    print()

    results = scan_watchlist(tickers, account_size)

    total = sum(len(ids) for ids in results.values())
    for ticker, ids in results.items():
        if ids:
            print(f"  {ticker}: {len(ids)} new trade(s) opened (IDs: {ids})")
        else:
            print(f"  {ticker}: no actionable signals")

    print(f"\nTotal new trades: {total}")


def cmd_check(args):
    print("Checking open positions for exit conditions ...")
    out = check_all_open_with_meta()
    results = out["results"]
    degraded_count = out.get("degraded_count", 0)
    sig = evaluate_pending_signals(limit=500)

    if not results:
        print("No open trades to check.")
        print(f"Signal eval: processed={sig['processed']}, updated={sig['updated']}")
        return

    closed = {tid: r for tid, r in results.items() if r != "OPEN"}
    still_open = {tid: r for tid, r in results.items() if r == "OPEN"}

    if closed:
        print(f"\nClosed {len(closed)} trade(s):")
        for tid, reason in closed.items():
            print(f"  Trade #{tid}: {reason}")

    print(f"Still open: {len(still_open)}")
    if degraded_count > 0:
        print(f"Degraded pricing marks: {degraded_count} trade(s) had missing option marks this cycle.")
    print(f"Signal eval: processed={sig['processed']}, updated={sig['updated']}")


def cmd_status(args):
    print(open_positions_report())


def cmd_report(args):
    print(overall_report())


def cmd_analyze(args):
    print(signal_attribution())


def cmd_eval_signals(args):
    res = evaluate_pending_signals(limit=args.limit)
    print(f"Evaluated signals: processed={res['processed']}, updated={res['updated']}")


def cmd_signal_report(args):
    print(signal_quality_report())


def cmd_compare_layers(args):
    print(signal_execution_bridge_report())


def cmd_history(args):
    print(trade_history(limit=args.limit))


def cmd_health(args):
    import os
    import subprocess
    from datetime import datetime

    print("\n═══ PAPERTRADER HEALTH ═══\n")

    # 1. Database
    db_path = db.DB_PATH
    if os.path.exists(db_path):
        size_kb = os.path.getsize(db_path) / 1024
        print(f"  Database:     {db_path} ({size_kb:.0f} KB)")
    else:
        print("  Database:     NOT FOUND")

    with db.get_conn() as conn:
        open_count = conn.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'").fetchone()[0]
        closed_count = conn.execute("SELECT COUNT(*) FROM trades WHERE status='CLOSED'").fetchone()[0]
        scan_count = conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
        last_scan = conn.execute("SELECT scan_time FROM scans ORDER BY id DESC LIMIT 1").fetchone()
        last_check = conn.execute("SELECT check_time FROM price_checks ORDER BY id DESC LIMIT 1").fetchone()

    print(f"  Total scans:  {scan_count}")
    print(f"  Open trades:  {open_count}")
    print(f"  Closed trades: {closed_count}")
    print(f"  Last scan:    {last_scan[0] if last_scan else 'never'}")
    print(f"  Last check:   {last_check[0] if last_check else 'never'}")

    # 2. Backend
    from .config import API_BASE
    try:
        import requests
        r = requests.get(f"{API_BASE}/health", timeout=5)
        if r.status_code == 200:
            print(f"  Backend:      RUNNING ({API_BASE})")
        else:
            print(f"  Backend:      ERROR (status {r.status_code})")
    except Exception:
        print(f"  Backend:      DOWN ({API_BASE})")

    # 3. Cron
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if "papertrader" in result.stdout:
            cron_lines = [l.strip() for l in result.stdout.splitlines()
                          if "papertrader" in l and not l.startswith("#")]
            print(f"  Cron jobs:    {len(cron_lines)} active")
            for line in cron_lines:
                sched = line.split("cd ")[0].strip() if "cd " in line else line[:20]
                cmd = "scan" if "scan" in line else "check" if "check" in line else "?"
                print(f"                  {cmd}: {sched}")
        else:
            print("  Cron jobs:    NOT INSTALLED")
    except Exception:
        print("  Cron jobs:    UNKNOWN (crontab not available)")

    # 4. Log file
    log_path = os.path.join(os.path.dirname(db_path), "cron.log")
    if os.path.exists(log_path):
        size_kb = os.path.getsize(log_path) / 1024
        mod_time = datetime.fromtimestamp(os.path.getmtime(log_path)).strftime("%Y-%m-%d %H:%M")
        print(f"  Cron log:     {size_kb:.0f} KB (last modified: {mod_time})")
    else:
        print("  Cron log:     no output yet (will appear after first cron run)")

    print()


def cmd_cron(args):
    import os
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print(CRON_TEMPLATE.format(project_dir=project_dir))


def cmd_ensure_backend(args):
    ok, msg = ensure_backend_available()
    print(f"Backend guard: {msg}")
    if not ok:
        sys.exit(1)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        prog="papertrader",
        description="Forward-testing framework for DealersEdge signals",
    )
    parser.add_argument(
        "--account-size", type=float, default=None,
        help="Portfolio size for contract sizing",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Scan tickers for new signals")
    p_scan.add_argument("tickers", nargs="*", help="Tickers to scan (default: watchlist)")

    p_check = sub.add_parser("check", help="Check open trades for exits")

    p_status = sub.add_parser("status", help="Show open positions + latest unrealized P&L")

    p_report = sub.add_parser("report", help="Overall performance report")

    p_analyze = sub.add_parser("analyze", help="Signal attribution analysis")
    p_eval_signals = sub.add_parser("eval-signals", help="Evaluate signal-layer outcomes")
    p_eval_signals.add_argument("--limit", type=int, default=500, help="Max signals to process")
    p_signal_report = sub.add_parser("signal-report", help="Signal-layer quality report")
    p_compare_layers = sub.add_parser("compare-layers", help="Compare signal and execution layers")

    p_history = sub.add_parser("history", help="Closed trade log")
    p_history.add_argument("--limit", type=int, default=50, help="Max trades to show")

    p_health = sub.add_parser("health", help="System health check")

    p_cron = sub.add_parser("cron", help="Print crontab snippet")
    p_ensure_backend = sub.add_parser("ensure-backend", help="Ensure backend is healthy (autostart if down)")

    args = parser.parse_args()

    db.init_db()

    dispatch = {
        "scan": cmd_scan,
        "check": cmd_check,
        "status": cmd_status,
        "report": cmd_report,
        "analyze": cmd_analyze,
        "eval-signals": cmd_eval_signals,
        "signal-report": cmd_signal_report,
        "compare-layers": cmd_compare_layers,
        "history": cmd_history,
        "health": cmd_health,
        "cron": cmd_cron,
        "ensure-backend": cmd_ensure_backend,
    }

    dispatch[args.command](args)


if __name__ == "__main__":
    main()
