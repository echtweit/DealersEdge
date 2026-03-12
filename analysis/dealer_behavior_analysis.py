"""
Dealer behavior analysis from raw scan snapshots (execution-agnostic).

Focus:
  - Regime-conditioned next-scan returns
  - Regime transition probabilities
  - Wall interaction bounce/break behavior
  - OPEX-conditioned behavior shifts

Usage:
  python analysis/dealer_behavior_analysis.py
  python analysis/dealer_behavior_analysis.py --days 30 \
      --db baseline=papertrader/papertrader_baseline.db \
      --db challenger_v1=papertrader/papertrader_challenger_v1.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
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
    parser = argparse.ArgumentParser(description="Dealer behavior analysis from raw scan data")
    parser.add_argument(
        "--db",
        action="append",
        default=[],
        help="Database spec as name=path. Repeat for multiple DBs.",
    )
    parser.add_argument("--days", type=int, default=60, help="Lookback window (calendar days)")
    parser.add_argument("--wall-threshold-pct", type=float, default=0.5, help="Near-wall threshold in percent")
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
            "challenger_v2=papertrader/papertrader_challenger_v2.db",
        ]
    specs: list[DbSpec] = []
    for spec in raw_specs:
        if "=" not in spec:
            raise ValueError(f"Invalid --db format: {spec}. Use name=path")
        name, path = spec.split("=", 1)
        specs.append(DbSpec(name=name.strip(), path=Path(path.strip())))
    return specs


def _level_strike(level) -> Optional[float]:
    if isinstance(level, (int, float)):
        return float(level)
    if isinstance(level, dict):
        s = level.get("strike")
        if isinstance(s, (int, float)):
            return float(s)
    return None


def _extract_walls(full_response_raw: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
    try:
        obj = json.loads(full_response_raw) if full_response_raw else {}
    except Exception:
        return None, None, None
    kl = obj.get("key_levels", {}) if isinstance(obj, dict) else {}
    call_wall = _level_strike(kl.get("call_wall"))
    put_wall = _level_strike(kl.get("put_wall"))
    max_pain = _level_strike(kl.get("max_pain"))
    if max_pain is None:
        mp = (obj.get("max_pain_profile", {}) or {}).get("max_pain")
        if isinstance(mp, (int, float)):
            max_pain = float(mp)
    return call_wall, put_wall, max_pain


def load_scans(db: DbSpec) -> pd.DataFrame:
    if not db.path.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(str(db.path))
    try:
        q = """
            SELECT
                id, ticker, scan_time, spot_price, gex_regime, reynolds_regime, acf_regime,
                is_monthly_opex, is_quarterly_opex, days_to_opex,
                max_pain_level, dist_to_max_pain_pct, full_response
            FROM scans
            ORDER BY ticker, scan_time
        """
        df = pd.read_sql_query(q, conn)
    finally:
        conn.close()
    if df.empty:
        return df
    df["profile"] = db.name
    parsed = df["full_response"].apply(_extract_walls)
    df["call_wall"] = parsed.apply(lambda x: x[0])
    df["put_wall"] = parsed.apply(lambda x: x[1])
    df["max_pain_level_from_json"] = parsed.apply(lambda x: x[2])
    df["max_pain_level"] = pd.to_numeric(df["max_pain_level"], errors="coerce").fillna(
        pd.to_numeric(df["max_pain_level_from_json"], errors="coerce")
    )
    df["scan_ts"] = pd.to_datetime(df["scan_time"], errors="coerce", utc=True)
    df["scan_date_ny"] = df["scan_ts"].dt.tz_convert(NY_TZ).dt.date
    return df


def enrich_forward_metrics(df: pd.DataFrame, wall_threshold_pct: float) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["spot_price"] = pd.to_numeric(out["spot_price"], errors="coerce")
    out = out.sort_values(["profile", "ticker", "scan_ts"])
    out["next_spot"] = out.groupby(["profile", "ticker"])["spot_price"].shift(-1)
    out["next_scan_ts"] = out.groupby(["profile", "ticker"])["scan_ts"].shift(-1)
    out["next_return_pct"] = (out["next_spot"] - out["spot_price"]) / out["spot_price"] * 100.0
    out["next_abs_return_pct"] = out["next_return_pct"].abs()

    out["call_wall_dist_pct"] = ((pd.to_numeric(out["call_wall"], errors="coerce") - out["spot_price"]).abs() /
                                 out["spot_price"] * 100.0)
    out["put_wall_dist_pct"] = ((out["spot_price"] - pd.to_numeric(out["put_wall"], errors="coerce")).abs() /
                                out["spot_price"] * 100.0)
    out["near_call_wall"] = out["call_wall_dist_pct"] <= wall_threshold_pct
    out["near_put_wall"] = out["put_wall_dist_pct"] <= wall_threshold_pct

    # "Bounce" / "break" are defined relative to next-scan move direction.
    out["call_wall_bounce"] = out["near_call_wall"] & (out["next_return_pct"] < -0.25)
    out["call_wall_break"] = out["near_call_wall"] & (out["next_return_pct"] > 0.25)
    out["put_wall_bounce"] = out["near_put_wall"] & (out["next_return_pct"] > 0.25)
    out["put_wall_break"] = out["near_put_wall"] & (out["next_return_pct"] < -0.25)

    # Regime state for transition matrix.
    out["regime_state"] = (
        out["gex_regime"].fillna("NA").astype(str) + "|" + out["reynolds_regime"].fillna("NA").astype(str)
    )
    out["next_regime_state"] = out.groupby(["profile", "ticker"])["regime_state"].shift(-1)
    return out


def chart_regime_heatmap(df: pd.DataFrame, outdir: Path):
    work = df.dropna(subset=["next_return_pct"])
    if work.empty:
        return
    pivot = work.pivot_table(
        index="gex_regime",
        columns="reynolds_regime",
        values="next_return_pct",
        aggfunc="mean",
    ).sort_index()
    pivot.to_csv(outdir / "regime_return_mean.csv")
    if pivot.empty:
        return
    fig, ax = plt.subplots(figsize=(10, 6))
    mat = ax.imshow(pivot.values, aspect="auto")
    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels(pivot.columns, rotation=30, ha="right")
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels(pivot.index)
    ax.set_title("Mean Next-Scan Return (%) by GEX x Reynolds")
    cbar = fig.colorbar(mat, ax=ax)
    cbar.set_label("Return %")
    fig.tight_layout()
    fig.savefig(outdir / "regime_return_heatmap.png", dpi=160)
    plt.close(fig)


def chart_transition_matrix(df: pd.DataFrame, outdir: Path):
    work = df.dropna(subset=["regime_state", "next_regime_state"])
    if work.empty:
        return
    counts = work.groupby(["regime_state", "next_regime_state"]).size().reset_index(name="n")
    probs = counts.copy()
    probs["p"] = probs["n"] / probs.groupby("regime_state")["n"].transform("sum")
    mat = probs.pivot(index="regime_state", columns="next_regime_state", values="p").fillna(0.0)
    mat.to_csv(outdir / "regime_transition_matrix.csv")
    if mat.empty:
        return
    fig, ax = plt.subplots(figsize=(10, 8))
    im = ax.imshow(mat.values, aspect="auto")
    ax.set_xticks(range(len(mat.columns)))
    ax.set_xticklabels(mat.columns, rotation=40, ha="right")
    ax.set_yticks(range(len(mat.index)))
    ax.set_yticklabels(mat.index)
    ax.set_title("Regime Transition Probability Matrix")
    cb = fig.colorbar(im, ax=ax)
    cb.set_label("Transition probability")
    fig.tight_layout()
    fig.savefig(outdir / "regime_transition_heatmap.png", dpi=160)
    plt.close(fig)


def chart_wall_interactions(df: pd.DataFrame, outdir: Path):
    work = df.dropna(subset=["next_return_pct"])
    if work.empty:
        return
    rows = []
    for profile, g in work.groupby("profile"):
        call_n = int(g["near_call_wall"].sum())
        put_n = int(g["near_put_wall"].sum())
        rows.append(
            {
                "profile": profile,
                "call_near_n": call_n,
                "call_bounce_rate": float(g["call_wall_bounce"].sum() / call_n * 100) if call_n else 0.0,
                "call_break_rate": float(g["call_wall_break"].sum() / call_n * 100) if call_n else 0.0,
                "put_near_n": put_n,
                "put_bounce_rate": float(g["put_wall_bounce"].sum() / put_n * 100) if put_n else 0.0,
                "put_break_rate": float(g["put_wall_break"].sum() / put_n * 100) if put_n else 0.0,
            }
        )
    panel = pd.DataFrame(rows)
    panel.to_csv(outdir / "wall_interactions.csv", index=False)
    if panel.empty:
        return
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
    x = range(len(panel))
    axes[0].bar([i - 0.18 for i in x], panel["call_bounce_rate"], width=0.35, label="Bounce")
    axes[0].bar([i + 0.18 for i in x], panel["call_break_rate"], width=0.35, label="Break")
    axes[0].set_xticks(list(x))
    axes[0].set_xticklabels(panel["profile"])
    axes[0].set_title("Near Call-Wall Outcomes")
    axes[0].set_ylabel("Rate %")
    axes[0].legend()
    axes[0].grid(axis="y", alpha=0.25)

    axes[1].bar([i - 0.18 for i in x], panel["put_bounce_rate"], width=0.35, label="Bounce")
    axes[1].bar([i + 0.18 for i in x], panel["put_break_rate"], width=0.35, label="Break")
    axes[1].set_xticks(list(x))
    axes[1].set_xticklabels(panel["profile"])
    axes[1].set_title("Near Put-Wall Outcomes")
    axes[1].legend()
    axes[1].grid(axis="y", alpha=0.25)

    fig.tight_layout()
    fig.savefig(outdir / "wall_interaction_panel.png", dpi=160)
    plt.close(fig)


def chart_opex_effect(df: pd.DataFrame, outdir: Path):
    work = df.dropna(subset=["next_abs_return_pct"]).copy()
    if work.empty:
        return
    work["opex_bucket"] = work["is_monthly_opex"].fillna(0).astype(int).map({1: "Monthly OPEX", 0: "Non-OPEX"})
    grp = (
        work.groupby(["profile", "opex_bucket"], as_index=False)["next_abs_return_pct"]
        .mean()
        .rename(columns={"next_abs_return_pct": "mean_abs_next_return_pct"})
    )
    grp.to_csv(outdir / "opex_effect.csv", index=False)
    if grp.empty:
        return
    fig, ax = plt.subplots(figsize=(10, 5))
    x_labels = []
    y_vals = []
    for _, r in grp.iterrows():
        x_labels.append(f"{r['profile']}|{r['opex_bucket']}")
        y_vals.append(r["mean_abs_next_return_pct"])
    x = list(range(len(x_labels)))
    ax.bar(x, y_vals)
    ax.set_title("Mean |Next-Scan Return| by OPEX Bucket")
    ax.set_ylabel("Abs return %")
    ax.set_xticks(x)
    ax.set_xticklabels(x_labels, rotation=30, ha="right")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(outdir / "opex_effect.png", dpi=160)
    plt.close(fig)


def write_summary(df: pd.DataFrame, outdir: Path):
    lines = ["# Dealer Behavior Summary", ""]
    lines.append(f"Generated: {datetime.now(NY_TZ).isoformat()}")
    lines.append("")
    if df.empty:
        lines.append("No scan data available in the selected window.")
        (outdir / "summary_behavior.md").write_text("\n".join(lines), encoding="utf-8")
        return

    lines.append("## Coverage")
    lines.append("")
    cov = df.groupby("profile").agg(
        scans=("id", "count"),
        tickers=("ticker", "nunique"),
        with_next=("next_return_pct", lambda s: int(s.notna().sum())),
    )
    for profile, r in cov.iterrows():
        lines.append(f"- `{profile}`: scans={int(r.scans)}, tickers={int(r.tickers)}, forward-points={int(r.with_next)}")

    lines.append("")
    lines.append("## Regime Return Snapshot")
    lines.append("")
    regime = (
        df.dropna(subset=["next_return_pct"])
        .groupby(["profile", "gex_regime", "reynolds_regime"], as_index=False)["next_return_pct"]
        .mean()
        .sort_values(["profile", "next_return_pct"], ascending=[True, False])
    )
    if regime.empty:
        lines.append("- Not enough forward data for regime return snapshot.")
    else:
        for profile, g in regime.groupby("profile"):
            top = g.head(3)
            lines.append(f"- `{profile}` top 3 mean next-scan return regimes:")
            for _, r in top.iterrows():
                lines.append(f"  - {r['gex_regime']} | {r['reynolds_regime']}: {r['next_return_pct']:.3f}%")

    lines.append("")
    lines.append("## Output Files")
    lines.append("")
    lines.append("- `scans_enriched.csv`")
    lines.append("- `regime_return_mean.csv`")
    lines.append("- `regime_transition_matrix.csv`")
    lines.append("- `wall_interactions.csv`")
    lines.append("- `opex_effect.csv`")
    lines.append("- `regime_return_heatmap.png`")
    lines.append("- `regime_transition_heatmap.png`")
    lines.append("- `wall_interaction_panel.png`")
    lines.append("- `opex_effect.png`")

    (outdir / "summary_behavior.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    args = parse_args()
    specs = parse_db_specs(args.db)
    out_base = Path(args.outdir)
    outdir = out_base / datetime.now(NY_TZ).strftime("%Y%m%d_%H%M%S") / "dealer_behavior"
    outdir.mkdir(parents=True, exist_ok=True)

    cutoff = datetime.now(NY_TZ).date() - timedelta(days=args.days)
    parts = []
    for spec in specs:
        df = load_scans(spec)
        if not df.empty:
            df = df[df["scan_date_ny"] >= cutoff]
        parts.append(df)
    all_scans = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    enriched = enrich_forward_metrics(all_scans, wall_threshold_pct=args.wall_threshold_pct)
    if not enriched.empty:
        enriched.to_csv(outdir / "scans_enriched.csv", index=False)

    chart_regime_heatmap(enriched, outdir)
    chart_transition_matrix(enriched, outdir)
    chart_wall_interactions(enriched, outdir)
    chart_opex_effect(enriched, outdir)
    write_summary(enriched, outdir)

    print(f"Dealer behavior analysis complete. Output: {outdir}")


if __name__ == "__main__":
    main()
