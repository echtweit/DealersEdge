"""
Statistical analysis pack for PaperTrader databases.

Generates:
  - markdown summary
  - CSV exports
  - chart images (PNG)

Usage:
  python analysis/stat_analysis.py
  python analysis/stat_analysis.py --days 30 \
      --db baseline=papertrader/papertrader_baseline.db \
      --db challenger_v1=papertrader/papertrader_challenger_v1.db
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

try:
    import matplotlib.pyplot as plt
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "matplotlib is required for chart generation. "
        "Install with: pip install matplotlib"
    ) from exc


NY_TZ = ZoneInfo("America/New_York")


@dataclass
class DbSpec:
    name: str
    path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate PaperTrader statistical chart pack")
    parser.add_argument(
        "--db",
        action="append",
        default=[],
        help="Database spec as name=path. Repeat for multiple DBs.",
    )
    parser.add_argument("--days", type=int, default=60, help="Lookback window (calendar days)")
    parser.add_argument(
        "--outdir",
        default="analysis/output",
        help="Base output directory for generated artifacts",
    )
    return parser.parse_args()


def parse_db_specs(raw_specs: list[str]) -> list[DbSpec]:
    if not raw_specs:
        raw_specs = [
            "baseline=papertrader/papertrader_baseline.db",
            "challenger_v1=papertrader/papertrader_challenger_v1.db",
        ]
    specs: list[DbSpec] = []
    for spec in raw_specs:
        if "=" not in spec:
            raise ValueError(f"Invalid --db format: {spec}. Use name=path")
        name, path = spec.split("=", 1)
        specs.append(DbSpec(name=name.strip(), path=Path(path.strip())))
    return specs


def read_trades(db_path: Path) -> pd.DataFrame:
    if not db_path.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(str(db_path))
    try:
        df = pd.read_sql_query("SELECT * FROM trades", conn)
    finally:
        conn.close()
    return df


def read_no_trade_events(db_path: Path) -> pd.DataFrame:
    if not db_path.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(str(db_path))
    try:
        # Some historical DBs may not have this table yet.
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='no_trade_events' LIMIT 1"
        ).fetchone()
        if not exists:
            return pd.DataFrame()
        df = pd.read_sql_query("SELECT * FROM no_trade_events", conn)
    finally:
        conn.close()
    return df


def to_ny_date(series: pd.Series) -> pd.Series:
    ts = pd.to_datetime(series, errors="coerce", utc=True)
    return ts.dt.tz_convert(NY_TZ).dt.date


def enrich(df: pd.DataFrame, label: str) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["profile"] = label
    out["entry_date_ny"] = to_ny_date(out["entry_time"])
    out["exit_date_ny"] = to_ny_date(out["exit_time"])
    return out


def metric_summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {
            "closed": 0,
            "open": 0,
            "win_rate": 0.0,
            "avg_pnl_pct": 0.0,
            "total_pnl": 0.0,
        }
    closed = df[df["status"] == "CLOSED"].copy()
    open_n = int((df["status"] == "OPEN").sum())
    pnl = pd.to_numeric(closed["pnl_pct"], errors="coerce").dropna()
    pnl_d = pd.to_numeric(closed["pnl_dollars"], errors="coerce").fillna(0.0)
    win_rate = float((pnl > 0).mean() * 100) if len(pnl) else 0.0
    return {
        "closed": int(len(closed)),
        "open": open_n,
        "win_rate": win_rate,
        "avg_pnl_pct": float(pnl.mean()) if len(pnl) else 0.0,
        "total_pnl": float(pnl_d.sum()),
    }


def save_daily_curves(all_closed: pd.DataFrame, outdir: Path):
    if all_closed.empty:
        return
    daily = (
        all_closed.groupby(["profile", "exit_date_ny"], as_index=False)["pnl_dollars"]
        .sum()
        .sort_values(["profile", "exit_date_ny"])
    )
    daily["cum_pnl"] = daily.groupby("profile")["pnl_dollars"].cumsum()
    daily.to_csv(outdir / "daily_pnl.csv", index=False)

    fig, axes = plt.subplots(2, 1, figsize=(12, 9), sharex=True)
    for profile, g in daily.groupby("profile"):
        axes[0].plot(g["exit_date_ny"], g["cum_pnl"], label=profile, linewidth=2)
    axes[0].set_title("Cumulative P&L by Profile")
    axes[0].set_ylabel("USD")
    axes[0].legend()
    axes[0].grid(alpha=0.25)

    for profile, g in daily.groupby("profile"):
        axes[1].plot(g["exit_date_ny"], g["pnl_dollars"], label=profile, linewidth=1.6)
    axes[1].set_title("Daily P&L by Profile")
    axes[1].set_ylabel("USD")
    axes[1].set_xlabel("Exit Date (NY)")
    axes[1].grid(alpha=0.25)
    axes[1].legend()

    fig.tight_layout()
    fig.savefig(outdir / "pnl_curves.png", dpi=160)
    plt.close(fig)


def save_exit_mix(all_closed: pd.DataFrame, outdir: Path):
    if all_closed.empty:
        return
    pivot = (
        all_closed.groupby(["profile", "exit_reason"]).size().unstack(fill_value=0).sort_index()
    )
    pivot.to_csv(outdir / "exit_mix.csv")
    ax = pivot.plot(kind="bar", stacked=True, figsize=(11, 6), colormap="tab20")
    ax.set_title("Exit Reason Mix by Profile")
    ax.set_xlabel("Profile")
    ax.set_ylabel("Trades")
    ax.grid(axis="y", alpha=0.25)
    plt.tight_layout()
    plt.savefig(outdir / "exit_mix.png", dpi=160)
    plt.close()


def save_thesis_expectancy(all_closed: pd.DataFrame, outdir: Path):
    if all_closed.empty:
        return
    work = all_closed.copy()
    work["pnl_pct"] = pd.to_numeric(work["pnl_pct"], errors="coerce")
    grp = (
        work.groupby(["profile", "thesis"], as_index=False)
        .agg(trades=("id", "count"), avg_pnl_pct=("pnl_pct", "mean"))
    )
    grp = grp[grp["trades"] >= 3]
    if grp.empty:
        return
    grp.to_csv(outdir / "thesis_expectancy.csv", index=False)
    fig, ax = plt.subplots(figsize=(12, 7))
    for i, (profile, g) in enumerate(grp.groupby("profile")):
        g = g.sort_values("avg_pnl_pct")
        y = [f"{profile}:{t}" for t in g["thesis"]]
        ax.barh(y, g["avg_pnl_pct"], alpha=0.75, label=profile)
    ax.axvline(0, color="black", linewidth=1)
    ax.set_title("Thesis Avg P&L% (min 3 trades)")
    ax.set_xlabel("Avg P&L%")
    ax.legend()
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(outdir / "thesis_expectancy.png", dpi=160)
    plt.close(fig)


def save_max_pain_panel(all_closed: pd.DataFrame, outdir: Path):
    if all_closed.empty or "dist_compression_pct" not in all_closed.columns:
        return
    work = all_closed.copy()
    work["dist_compression_pct"] = pd.to_numeric(work["dist_compression_pct"], errors="coerce")
    work["hit_max_pain_flag"] = pd.to_numeric(work["hit_max_pain_flag"], errors="coerce")
    work = work.dropna(subset=["dist_compression_pct"])
    if work.empty:
        return
    by_profile = []
    for profile, g in work.groupby("profile"):
        hit = (g["hit_max_pain_flag"] == 1).mean() * 100 if len(g) else 0
        by_profile.append({"profile": profile, "hit_rate": hit, "n": len(g)})
    pd.DataFrame(by_profile).to_csv(outdir / "max_pain_metrics.csv", index=False)

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    work.boxplot(column="dist_compression_pct", by="profile", ax=axes[0])
    axes[0].set_title("Distance Compression to Max Pain")
    axes[0].set_ylabel("Compression %")
    axes[0].grid(axis="y", alpha=0.25)
    axes[0].set_xlabel("")

    x = [r["profile"] for r in by_profile]
    y = [r["hit_rate"] for r in by_profile]
    axes[1].bar(x, y, color=["#4c78a8", "#f58518", "#54a24b"][: len(x)])
    axes[1].set_title("Hit Rate Near Max Pain (<=0.25%)")
    axes[1].set_ylabel("Hit Rate %")
    axes[1].set_ylim(0, 100)
    axes[1].grid(axis="y", alpha=0.25)

    fig.suptitle("")
    fig.tight_layout()
    fig.savefig(outdir / "max_pain_panel.png", dpi=160)
    plt.close(fig)


def write_summary(summary_rows: list[dict], all_closed: pd.DataFrame, outdir: Path):
    lines = ["# Statistical Analysis Summary", ""]
    lines.append(f"Generated: {datetime.now(NY_TZ).isoformat()}")
    lines.append("")
    lines.append("## Profile Overview")
    lines.append("")
    lines.append("| Profile | Closed | Open | Win Rate | Avg P&L% | Total P&L $ |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for r in summary_rows:
        lines.append(
            f"| {r['profile']} | {r['closed']} | {r['open']} | {r['win_rate']:.1f}% | "
            f"{r['avg_pnl_pct']:.1f}% | {r['total_pnl']:.2f} |"
        )
    lines.append("")
    if not all_closed.empty:
        lines.append("## Top Exit Reasons")
        lines.append("")
        top = (
            all_closed.groupby(["profile", "exit_reason"]).size().reset_index(name="n")
            .sort_values(["profile", "n"], ascending=[True, False])
        )
        for profile, g in top.groupby("profile"):
            top3 = ", ".join(f"{row.exit_reason}:{int(row.n)}" for row in g.head(3).itertuples())
            lines.append(f"- `{profile}`: {top3}")
    lines.append("")
    lines.append("## Output Files")
    lines.append("")
    lines.append("- `daily_pnl.csv`")
    lines.append("- `exit_mix.csv`")
    lines.append("- `thesis_expectancy.csv` (if enough sample)")
    lines.append("- `max_pain_metrics.csv`")
    no_trade_path = outdir / "no_trade_reason_mix.csv"
    if no_trade_path.exists():
        lines.append("- `no_trade_reason_mix.csv`")
    lines.append("- `pnl_curves.png`")
    lines.append("- `exit_mix.png`")
    lines.append("- `thesis_expectancy.png` (if enough sample)")
    lines.append("- `max_pain_panel.png`")
    (outdir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    args = parse_args()
    specs = parse_db_specs(args.db)

    base_out = Path(args.outdir)
    ts = datetime.now(NY_TZ).strftime("%Y%m%d_%H%M%S")
    outdir = base_out / ts
    outdir.mkdir(parents=True, exist_ok=True)

    cutoff = datetime.now(NY_TZ).date() - timedelta(days=args.days)
    profile_frames = []
    summary_rows = []
    no_trade_frames = []

    for spec in specs:
        trades = enrich(read_trades(spec.path), spec.name)
        if not trades.empty:
            closed = trades[trades["status"] == "CLOSED"].copy()
            if "exit_date_ny" in closed.columns:
                closed = closed[closed["exit_date_ny"] >= cutoff]
            profile_frames.append(closed)
        no_trade = read_no_trade_events(spec.path)
        if not no_trade.empty:
            no_trade["profile"] = spec.name
            no_trade["event_date_ny"] = to_ny_date(no_trade["event_time"])
            no_trade = no_trade[no_trade["event_date_ny"] >= cutoff]
            no_trade_frames.append(no_trade)
        s = metric_summary(trades)
        summary_rows.append({"profile": spec.name, **s})

    all_closed = pd.concat(profile_frames, ignore_index=True) if profile_frames else pd.DataFrame()
    if not all_closed.empty:
        all_closed.to_csv(outdir / "closed_trades_raw.csv", index=False)
    all_no_trade = pd.concat(no_trade_frames, ignore_index=True) if no_trade_frames else pd.DataFrame()
    if not all_no_trade.empty:
        mix = (
            all_no_trade.groupby(["profile", "reason_code"], as_index=False)
            .size()
            .rename(columns={"size": "n"})
            .sort_values(["profile", "n"], ascending=[True, False])
        )
        mix.to_csv(outdir / "no_trade_reason_mix.csv", index=False)

    save_daily_curves(all_closed, outdir)
    save_exit_mix(all_closed, outdir)
    save_thesis_expectancy(all_closed, outdir)
    save_max_pain_panel(all_closed, outdir)
    write_summary(summary_rows, all_closed, outdir)

    print(f"Analysis complete. Output: {outdir}")


if __name__ == "__main__":
    main()
