"""
Counterfactual backtest: apply challenger_v3 entry filters to historical closed
trades from baseline/v1/v2 databases.

This is an entry-filter replay only: P&L is taken from original historical exits,
so it estimates selection effect, not full v3 execution behavior.

Usage:
  python analysis/v3_counterfactual.py
  python analysis/v3_counterfactual.py --days 30 \
      --db baseline=papertrader/papertrader_baseline.db \
      --db challenger_v1=papertrader/papertrader_challenger_v1.db \
      --db challenger_v2=papertrader/papertrader_challenger_v2.db
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


NY_TZ = ZoneInfo("America/New_York")
_CONFIDENCE_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


@dataclass
class DbSpec:
    name: str
    path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Counterfactual v3 filter replay from historical trades"
    )
    parser.add_argument(
        "--db",
        action="append",
        default=[],
        help="Database spec as name=path. Repeatable.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Lookback window by entry date (calendar days).",
    )
    parser.add_argument("--outdir", default="analysis/output", help="Base output directory.")

    # v3 config mirrors (defaults aligned with current v3 settings).
    parser.add_argument("--block-against-dealer", type=int, default=1)
    parser.add_argument("--against-dealer-override-min-conf", default="HIGH")
    parser.add_argument("--max-reynolds", type=float, default=1.0)
    parser.add_argument("--require-pg-laminar", type=int, default=0)
    parser.add_argument("--min-directional-dte", type=int, default=3)
    parser.add_argument("--dte-bypass-min-conf", default="HIGH")
    parser.add_argument("--blocked-theses", default="NEUTRAL")
    parser.add_argument("--blocked-tickers", default="AMZN,AAPL,NVDA,SPY,DASH")
    parser.add_argument("--skip-straddle-turbulent", type=int, default=1)
    return parser.parse_args()


def parse_db_specs(raw_specs: list[str]) -> list[DbSpec]:
    if not raw_specs:
        raw_specs = [
            "baseline=papertrader/papertrader_baseline.db",
            "challenger_v1=papertrader/papertrader_challenger_v1.db",
            "challenger_v2=papertrader/papertrader_challenger_v2.db",
        ]
    specs: list[DbSpec] = []
    for spec in raw_specs:
        if "=" not in spec:
            raise ValueError(f"Invalid --db format: {spec}. Use name=path")
        name, path = spec.split("=", 1)
        specs.append(DbSpec(name=name.strip(), path=Path(path.strip())))
    return specs


def _to_ny_date(series: pd.Series) -> pd.Series:
    ts = pd.to_datetime(series, errors="coerce", utc=True)
    return ts.dt.tz_convert(NY_TZ).dt.date


def _read_closed_trades(db: DbSpec) -> pd.DataFrame:
    if not db.path.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(str(db.path))
    try:
        query = """
            SELECT
                id, ticker, trade_type, option_type,
                entry_time, exit_time, dte_at_entry,
                confidence, gex_regime, reynolds_number, reynolds_regime,
                thesis, edge_type, pnl_pct, pnl_dollars, exit_reason
            FROM trades
            WHERE status = 'CLOSED'
            ORDER BY entry_time
        """
        df = pd.read_sql_query(query, conn)
    finally:
        conn.close()
    if df.empty:
        return df
    df["profile"] = db.name
    df["entry_date_ny"] = _to_ny_date(df["entry_time"])
    return df


def _conf_at_least(confidence: str | None, minimum: str) -> bool:
    current = _CONFIDENCE_ORDER.get((confidence or "").upper(), -1)
    required = _CONFIDENCE_ORDER.get((minimum or "").upper(), 99)
    return current >= required


def _is_breakout(thesis: str | None) -> bool:
    return (thesis or "").upper() == "MOMENTUM_BREAKOUT"


def _is_directional(trade_type: str | None) -> bool:
    return (trade_type or "").lower() == "directional"


def _is_straddle(trade_type: str | None) -> bool:
    return (trade_type or "").lower() == "straddle"


def _normalize_set(raw: str) -> set[str]:
    return {x.strip().upper() for x in (raw or "").split(",") if x.strip()}


def apply_v3_filter(df: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    if df.empty:
        return df

    blocked_theses = _normalize_set(args.blocked_theses)
    blocked_tickers = _normalize_set(args.blocked_tickers)

    def evaluate(row: pd.Series) -> tuple[int, str]:
        ticker = (row.get("ticker") or "").upper()
        thesis = (row.get("thesis") or "").upper()
        conf = (row.get("confidence") or "").upper()
        edge = (row.get("edge_type") or "").upper()
        trade_type = (row.get("trade_type") or "").lower()
        re_regime = (row.get("reynolds_regime") or "").upper()
        gex = (row.get("gex_regime") or "")
        re_num = pd.to_numeric(row.get("reynolds_number"), errors="coerce")
        dte = pd.to_numeric(row.get("dte_at_entry"), errors="coerce")
        dte = int(dte) if pd.notna(dte) else 0

        if ticker in blocked_tickers:
            return 0, "blocked_ticker"

        if (
            _is_straddle(trade_type)
            and int(args.skip_straddle_turbulent) == 1
            and re_regime == "TURBULENT"
        ):
            return 0, "straddle_turbulent"

        if _is_directional(trade_type):
            if thesis in blocked_theses:
                return 0, "blocked_thesis"

            if int(args.block_against_dealer) == 1 and edge == "AGAINST_DEALER":
                if not _conf_at_least(conf, args.against_dealer_override_min_conf):
                    return 0, "against_dealer_block"
                if re_regime != "LAMINAR":
                    return 0, "against_dealer_not_laminar"

            if pd.notna(re_num) and float(re_num) > float(args.max_reynolds):
                if not (
                    _is_breakout(thesis)
                    and edge == "WITH_DEALER"
                    and _conf_at_least(conf, "HIGH")
                ):
                    return 0, "reynolds_gate"

            if int(args.require_pg_laminar) == 1:
                if not (gex == "POSITIVE_GAMMA" and re_regime == "LAMINAR"):
                    return 0, "require_pg_laminar"

            high_conf = _conf_at_least(conf, args.dte_bypass_min_conf)
            if dte < int(args.min_directional_dte) and not high_conf:
                return 0, "dte_gate"

        return 1, "pass"

    assessed = df.copy()
    out = assessed.apply(evaluate, axis=1, result_type="expand")
    assessed["v3_keep"] = out[0].astype(int)
    assessed["v3_reason"] = out[1]
    return assessed


def _profile_metrics(df: pd.DataFrame, label: str) -> dict:
    if df.empty:
        return {
            "bucket": label,
            "trades": 0,
            "win_rate": 0.0,
            "avg_pnl_pct": 0.0,
            "median_pnl_pct": 0.0,
            "total_pnl_dollars": 0.0,
        }
    pnl_pct = pd.to_numeric(df["pnl_pct"], errors="coerce").dropna()
    pnl_dollars = pd.to_numeric(df["pnl_dollars"], errors="coerce").fillna(0.0)
    return {
        "bucket": label,
        "trades": int(len(df)),
        "win_rate": float((pnl_pct > 0).mean() * 100) if len(pnl_pct) else 0.0,
        "avg_pnl_pct": float(pnl_pct.mean()) if len(pnl_pct) else 0.0,
        "median_pnl_pct": float(pnl_pct.median()) if len(pnl_pct) else 0.0,
        "total_pnl_dollars": float(pnl_dollars.sum()),
    }


def write_summary(assessed: pd.DataFrame, outdir: Path, args: argparse.Namespace) -> None:
    kept = assessed[assessed["v3_keep"] == 1].copy()
    dropped = assessed[assessed["v3_keep"] == 0].copy()

    rows = [
        _profile_metrics(assessed, "all_input_trades"),
        _profile_metrics(kept, "kept_by_v3_filter"),
        _profile_metrics(dropped, "rejected_by_v3_filter"),
    ]
    metrics_df = pd.DataFrame(rows)
    metrics_df.to_csv(outdir / "counterfactual_metrics.csv", index=False)

    reason_counts = (
        dropped.groupby("v3_reason")
        .size()
        .reset_index(name="n")
        .sort_values("n", ascending=False)
        if not dropped.empty
        else pd.DataFrame(columns=["v3_reason", "n"])
    )
    reason_counts.to_csv(outdir / "rejection_reasons.csv", index=False)

    by_profile = (
        assessed.groupby(["profile", "v3_keep"])
        .size()
        .reset_index(name="n")
        .pivot(index="profile", columns="v3_keep", values="n")
        .fillna(0)
        .rename(columns={0: "rejected", 1: "kept"})
    )
    if not by_profile.empty:
        by_profile["keep_rate_pct"] = (
            by_profile["kept"] / (by_profile["kept"] + by_profile["rejected"]) * 100
        ).round(2)
        by_profile = by_profile.reset_index()
        by_profile.to_csv(outdir / "profile_keep_rates.csv", index=False)

    lines = ["# v3 Counterfactual Replay", ""]
    lines.append(f"Generated: {datetime.now(NY_TZ).isoformat()}")
    lines.append("")
    lines.append("## Scope")
    lines.append("")
    lines.append("- Entry-filter replay only: applies v3 entry gates to historical closed trades.")
    lines.append("- P&L values come from original historical trades/exits (selection-effect estimate).")
    lines.append("")
    lines.append("## High-Level Metrics")
    lines.append("")
    for row in rows:
        lines.append(
            f"- `{row['bucket']}`: n={row['trades']}, win={row['win_rate']:.1f}%, "
            f"avg={row['avg_pnl_pct']:.2f}%, median={row['median_pnl_pct']:.2f}%, "
            f"total=${row['total_pnl_dollars']:.2f}"
        )

    if not reason_counts.empty:
        lines.append("")
        lines.append("## Top Rejection Reasons")
        lines.append("")
        for row in reason_counts.head(8).itertuples(index=False):
            lines.append(f"- `{row.v3_reason}`: {int(row.n)}")

    lines.append("")
    lines.append("## Filter Parameters")
    lines.append("")
    lines.append(f"- `block_against_dealer`: {int(args.block_against_dealer)}")
    lines.append(
        f"- `against_dealer_override_min_conf`: {args.against_dealer_override_min_conf.upper()}"
    )
    lines.append(f"- `max_reynolds`: {float(args.max_reynolds):.2f}")
    lines.append(f"- `require_pg_laminar`: {int(args.require_pg_laminar)}")
    lines.append(f"- `min_directional_dte`: {int(args.min_directional_dte)}")
    lines.append(f"- `dte_bypass_min_conf`: {args.dte_bypass_min_conf.upper()}")
    lines.append(f"- `blocked_theses`: {args.blocked_theses}")
    lines.append(f"- `blocked_tickers`: {args.blocked_tickers}")
    lines.append(f"- `skip_straddle_turbulent`: {int(args.skip_straddle_turbulent)}")

    lines.append("")
    lines.append("## Output Files")
    lines.append("")
    lines.append("- `trades_assessed.csv`")
    lines.append("- `counterfactual_metrics.csv`")
    lines.append("- `profile_keep_rates.csv`")
    lines.append("- `rejection_reasons.csv`")
    lines.append("- `summary_v3_counterfactual.md`")

    (outdir / "summary_v3_counterfactual.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    specs = parse_db_specs(args.db)

    outdir = (
        Path(args.outdir)
        / datetime.now(NY_TZ).strftime("%Y%m%d_%H%M%S")
        / "v3_counterfactual"
    )
    outdir.mkdir(parents=True, exist_ok=True)

    cutoff = datetime.now(NY_TZ).date() - timedelta(days=args.days)
    chunks = []
    for spec in specs:
        df = _read_closed_trades(spec)
        if not df.empty:
            df = df[df["entry_date_ny"] >= cutoff]
            chunks.append(df)

    all_closed = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
    assessed = apply_v3_filter(all_closed, args)
    if not assessed.empty:
        assessed.to_csv(outdir / "trades_assessed.csv", index=False)

    write_summary(assessed, outdir, args)
    print(f"Counterfactual analysis complete. Output: {outdir}")


if __name__ == "__main__":
    main()
