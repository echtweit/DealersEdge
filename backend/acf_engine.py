"""
ACF Regime Detection — ported from OscillationPriming.
Lag-1 autocorrelation of intraday returns reveals whether
dealers are dampening (long gamma) or amplifying (short gamma).

Panel average: ACF1 ~ -0.203 across 37 tickers.
~92.7% of days are dampened; ~7.3% amplified.
Phase transition at ~12.9% amplified days.
"""

import numpy as np
import pandas as pd
import yfinance as yf
from typing import Optional

SHORT_GAMMA_CEILING = 0.11


def compute_daily_acf(prices: np.ndarray, max_lag: int = 10) -> np.ndarray:
    returns = pd.Series(prices).pct_change().dropna().values
    if len(returns) < max_lag + 10:
        return np.full(max_lag, np.nan)

    n = len(returns)
    mean = returns.mean()
    var = np.var(returns)
    if var < 1e-12:
        return np.full(max_lag, np.nan)

    acf = np.zeros(max_lag)
    for lag in range(1, max_lag + 1):
        acf[lag - 1] = np.mean((returns[:n - lag] - mean) * (returns[lag:] - mean)) / var
    return acf


def classify_regime(acf_lag1: float) -> str:
    if acf_lag1 < -0.05:
        return "LONG_GAMMA"
    elif acf_lag1 > 0.05:
        return "SHORT_GAMMA"
    return "NEUTRAL"


def scan_ticker_acf(symbol: str, period: str = "5d", interval: str = "2m") -> dict:
    """
    Scan a ticker's recent intraday data for gamma regime via ACF.
    Uses yfinance directly — no external dependencies needed.
    """
    try:
        stock = yf.Ticker(symbol)
        df = stock.history(period=period, interval=interval)
    except Exception:
        return {"symbol": symbol, "status": "NO_DATA"}

    if df.empty or len(df) < 30:
        return {"symbol": symbol, "status": "NO_DATA"}

    daily_results = []
    for date, day_df in df.groupby(df.index.date):
        if len(day_df) < 20:
            continue

        acf = compute_daily_acf(day_df["Close"].values, max_lag=5)
        lag1 = float(acf[0])
        if np.isnan(lag1):
            continue

        regime = classify_regime(lag1)
        daily_results.append({
            "date": str(date),
            "lag1_acf": round(lag1, 4),
            "regime": regime,
            "n_bars": len(day_df),
        })

    if not daily_results:
        return {"symbol": symbol, "status": "INSUFFICIENT_DATA"}

    lag1_values = [d["lag1_acf"] for d in daily_results]
    n_dampened = sum(1 for v in lag1_values if v < -0.05)
    n_amplified = sum(1 for v in lag1_values if v > 0.05)
    n_total = len(lag1_values)

    mean_acf1 = float(np.nanmean(lag1_values))
    overall_regime = classify_regime(mean_acf1)

    # ACF decay trend
    direction = "STABLE"
    slope = 0.0
    if len(lag1_values) >= 3:
        x = np.arange(len(lag1_values), dtype=float)
        slope, _ = np.polyfit(x, lag1_values, 1)
        if slope < -0.005:
            direction = "DEEPENING"
        elif slope > 0.005:
            direction = "SHALLOWING"

    # Regime stability
    transitions = 0
    for i in range(1, len(lag1_values)):
        prev_sign = 1 if lag1_values[i - 1] > 0 else -1
        curr_sign = 1 if lag1_values[i] > 0 else -1
        if prev_sign != curr_sign:
            transitions += 1

    rate = transitions / n_total if n_total > 0 else 0
    if rate < 0.10:
        stability = "ROCK_SOLID"
    elif rate < 0.25:
        stability = "STABLE"
    elif rate < 0.40:
        stability = "CONTESTED"
    else:
        stability = "UNRELIABLE"

    # Squeeze ceiling proximity
    max_recent = max(lag1_values[-3:]) if len(lag1_values) >= 3 else max(lag1_values)
    at_ceiling = max_recent >= SHORT_GAMMA_CEILING * 0.85

    # Self-excitation index (Hawkes-inspired)
    all_prices = df["Close"].values
    self_excitation = compute_self_excitation(all_prices)

    return {
        "symbol": symbol,
        "status": "OK",
        "n_days": n_total,
        "mean_acf1": round(mean_acf1, 4),
        "regime": overall_regime,
        "pct_dampened": round(100 * n_dampened / n_total, 1),
        "pct_amplified": round(100 * n_amplified / n_total, 1),
        "acf_trend": direction,
        "acf_slope": round(float(slope), 6),
        "stability": stability,
        "transitions_per_day": round(rate, 3),
        "at_squeeze_ceiling": at_ceiling,
        "daily_results": daily_results,
        "self_excitation": self_excitation,
    }


def compute_self_excitation(prices: np.ndarray, threshold_pct: float = 0.1) -> dict:
    """
    Hawkes-inspired self-excitation index from intraday prices.
    Measures how often and how intensely moves cluster in the same direction —
    the hallmark of dealer amplification in negative gamma.
    """
    returns = pd.Series(prices).pct_change().dropna().values
    if len(returns) < 20:
        return _empty_sei()

    threshold = threshold_pct / 100
    clusters = []
    current_cluster = []
    last_sign = 0

    for r in returns:
        if abs(r) >= threshold:
            sign = 1 if r > 0 else -1
            if sign == last_sign or last_sign == 0:
                current_cluster.append(abs(r))
                last_sign = sign
            else:
                if len(current_cluster) >= 2:
                    clusters.append(current_cluster[:])
                current_cluster = [abs(r)]
                last_sign = sign
        else:
            if len(current_cluster) >= 2:
                clusters.append(current_cluster[:])
            current_cluster = []
            last_sign = 0

    if len(current_cluster) >= 2:
        clusters.append(current_cluster)

    if not clusters:
        return _empty_sei()

    cluster_scores = [len(c) * sum(c) * 10000 for c in clusters]
    sei = float(np.mean(cluster_scores))
    max_cluster = max(len(c) for c in clusters)
    avg_size = float(np.mean([len(c) for c in clusters]))

    if sei > 150:
        regime = "HIGH_EXCITATION"
        desc = "Strong self-exciting feedback — moves amplify rapidly"
    elif sei > 80:
        regime = "MODERATE_EXCITATION"
        desc = "Some self-exciting behavior — occasional momentum bursts"
    elif sei > 40:
        regime = "LOW_EXCITATION"
        desc = "Weak self-excitation — moves don't consistently amplify"
    else:
        regime = "NONE"
        desc = "No meaningful self-excitation — mean-reversion dominant"

    return {
        "sei": round(sei, 3),
        "regime": regime,
        "description": desc,
        "n_clusters": len(clusters),
        "avg_cluster_size": round(avg_size, 1),
        "max_cluster_size": max_cluster,
        "total_excitation_events": sum(len(c) for c in clusters),
    }


def _empty_sei():
    return {
        "sei": 0, "regime": "NONE", "description": "Insufficient data",
        "n_clusters": 0, "avg_cluster_size": 0, "max_cluster_size": 0,
        "total_excitation_events": 0,
    }
