"""
Structural Technicals — MA alignment, ATR, and Relative Strength.
Provides trend context that overlays on top of dealer positioning data.

These metrics answer three questions the dealer model alone cannot:
  1. Is price structurally trending or range-bound? (MAs)
  2. How big are typical moves? (ATR — used for breakeven analysis)
  3. Is this name leading or lagging the market? (RS)
"""

import numpy as np
import yfinance as yf
from typing import Optional


def compute_technicals(
    ticker: str,
    price_history: Optional[list[dict]] = None,
    benchmark: str = "SPY",
) -> dict:
    if not price_history or len(price_history) < 20:
        try:
            stock = yf.Ticker(ticker)
            df = stock.history(period="1y", interval="1d")
            price_history = [
                {
                    "date": ts.strftime("%Y-%m-%d"),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": int(row["Volume"]),
                }
                for ts, row in df.iterrows()
            ]
        except Exception:
            return _empty_technicals()

    if len(price_history) < 20:
        return _empty_technicals()

    closes = np.array([bar["close"] for bar in price_history])
    highs = np.array([bar["high"] for bar in price_history])
    lows = np.array([bar["low"] for bar in price_history])
    spot = closes[-1]

    # --- Moving Averages ---
    ma_data = _compute_moving_averages(closes, spot)

    # --- ATR ---
    atr_data = _compute_atr(closes, highs, lows, spot)

    # --- Relative Strength vs Benchmark ---
    rs_data = _compute_relative_strength(ticker, price_history, benchmark)

    # --- VWAP ---
    volumes = np.array([bar.get("volume", 0) for bar in price_history])
    opens = np.array([bar["open"] for bar in price_history])
    vwap_data = _compute_vwap(opens, highs, lows, closes, volumes, spot)

    # --- Trend Classification ---
    trend = _classify_trend(ma_data, atr_data, rs_data, spot)

    return {
        "moving_averages": ma_data,
        "atr": atr_data,
        "relative_strength": rs_data,
        "vwap": vwap_data,
        "trend": trend,
    }


def _compute_moving_averages(closes, spot):
    n = len(closes)
    result = {}

    for period, label in [(20, "sma_20"), (50, "sma_50"), (200, "sma_200")]:
        if n >= period:
            sma = float(np.mean(closes[-period:]))
            slope_window = min(5, period)
            sma_prev = float(np.mean(closes[-(period + slope_window):-slope_window])) if n >= period + slope_window else sma
            slope_pct = ((sma - sma_prev) / sma_prev * 100) if sma_prev > 0 else 0

            result[label] = {
                "value": round(sma, 2),
                "distance_pct": round((spot - sma) / sma * 100, 2),
                "position": "ABOVE" if spot > sma else "BELOW",
                "slope": "RISING" if slope_pct > 0.1 else "FALLING" if slope_pct < -0.1 else "FLAT",
                "slope_pct": round(slope_pct, 2),
            }
        else:
            result[label] = None

    # MA alignment score: +3 above all, +2 above 20+50, +1 above 20, 0 mixed, negatives for below
    alignment = 0
    alignment_labels = []
    for key in ["sma_20", "sma_50", "sma_200"]:
        if result.get(key):
            if result[key]["position"] == "ABOVE":
                alignment += 1
                alignment_labels.append(f"Above {key.upper().replace('SMA_', '')}")
            else:
                alignment -= 1
                alignment_labels.append(f"Below {key.upper().replace('SMA_', '')}")

    if alignment == 3:
        alignment_label = "FULL_BULL"
        alignment_desc = "Price above all major MAs — strong uptrend structure"
    elif alignment == 2:
        alignment_label = "BULL"
        alignment_desc = "Price above most MAs — uptrend intact"
    elif alignment == 1:
        alignment_label = "MIXED_BULL"
        alignment_desc = "Mixed signals leaning bullish"
    elif alignment == 0:
        alignment_label = "NEUTRAL"
        alignment_desc = "Mixed MA alignment — no clear trend"
    elif alignment == -1:
        alignment_label = "MIXED_BEAR"
        alignment_desc = "Mixed signals leaning bearish"
    elif alignment == -2:
        alignment_label = "BEAR"
        alignment_desc = "Price below most MAs — downtrend intact"
    else:
        alignment_label = "FULL_BEAR"
        alignment_desc = "Price below all major MAs — strong downtrend structure"

    # Golden/Death cross detection
    cross = None
    sma50 = result.get("sma_50")
    sma200 = result.get("sma_200")
    if sma50 and sma200:
        diff_pct = abs(sma50["value"] - sma200["value"]) / sma200["value"] * 100
        if sma50["value"] > sma200["value"] and diff_pct < 1.5 and sma50["slope"] == "RISING":
            cross = "GOLDEN_CROSS_RECENT"
        elif sma50["value"] < sma200["value"] and diff_pct < 1.5 and sma50["slope"] == "FALLING":
            cross = "DEATH_CROSS_RECENT"

    result["alignment"] = alignment
    result["alignment_label"] = alignment_label
    result["alignment_desc"] = alignment_desc
    result["alignment_details"] = alignment_labels
    result["cross"] = cross

    return result


def _compute_atr(closes, highs, lows, spot, period=14):
    n = len(closes)
    if n < period + 1:
        return {"atr": 0, "atr_pct": 0, "period": period, "daily_range_avg": 0}

    true_ranges = []
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)

    # Wilder's smoothed ATR
    atr = float(np.mean(true_ranges[-period:]))
    atr_pct = (atr / spot) * 100

    # Recent 5-day avg range for short-term context
    recent_ranges = [highs[i] - lows[i] for i in range(-5, 0)]
    recent_range = float(np.mean(recent_ranges)) if recent_ranges else atr
    recent_range_pct = (recent_range / spot) * 100

    # ATR trend: is vol expanding or contracting?
    if len(true_ranges) >= period * 2:
        atr_prev = float(np.mean(true_ranges[-(period * 2):-period]))
        atr_change = ((atr - atr_prev) / atr_prev * 100) if atr_prev > 0 else 0
        atr_trend = "EXPANDING" if atr_change > 10 else "CONTRACTING" if atr_change < -10 else "STABLE"
    else:
        atr_change = 0
        atr_trend = "STABLE"

    return {
        "atr": round(atr, 2),
        "atr_pct": round(atr_pct, 2),
        "period": period,
        "recent_range": round(recent_range, 2),
        "recent_range_pct": round(recent_range_pct, 2),
        "atr_trend": atr_trend,
        "atr_change_pct": round(atr_change, 1),
    }


def _compute_relative_strength(ticker, price_history, benchmark="SPY"):
    """
    Mansfield Relative Strength: measures ticker performance vs benchmark
    over multiple timeframes. Positive = outperforming, negative = lagging.
    """
    if ticker.upper() == benchmark.upper():
        return {
            "benchmark": benchmark,
            "rs_5d": 0, "rs_20d": 0, "rs_60d": 0,
            "rs_trend": "N/A",
            "rs_label": "IS_BENCHMARK",
            "rs_desc": "This is the benchmark itself",
            "beta_60d": 1.0, "beta_20d": 1.0, "beta_adj_factor": 1.0,
        }

    try:
        bench_stock = yf.Ticker(benchmark)
        bench_df = bench_stock.history(period="6mo", interval="1d")
        if bench_df.empty:
            return _empty_rs(benchmark)
        bench_closes = bench_df["Close"].values
    except Exception:
        return _empty_rs(benchmark)

    ticker_closes = np.array([bar["close"] for bar in price_history])

    # Align lengths
    min_len = min(len(ticker_closes), len(bench_closes))
    if min_len < 20:
        return _empty_rs(benchmark)

    tc = ticker_closes[-min_len:]
    bc = bench_closes[-min_len:]

    rs_results = {}
    for days, label in [(5, "rs_5d"), (20, "rs_20d"), (60, "rs_60d")]:
        if min_len >= days:
            ticker_ret = (tc[-1] / tc[-days] - 1) * 100
            bench_ret = (bc[-1] / bc[-days] - 1) * 100
            rs_results[label] = round(ticker_ret - bench_ret, 2)
        else:
            rs_results[label] = 0

    # Beta: covariance(ticker_returns, bench_returns) / variance(bench_returns)
    beta_60 = 1.0
    beta_20 = 1.0
    if min_len >= 61:
        tc_r = np.diff(tc[-61:]) / tc[-61:-1]
        bc_r = np.diff(bc[-61:]) / bc[-61:-1]
        var_b = float(np.var(bc_r))
        beta_60 = float(np.cov(tc_r, bc_r)[0, 1] / var_b) if var_b > 1e-10 else 1.0
    if min_len >= 21:
        tc_r = np.diff(tc[-21:]) / tc[-21:-1]
        bc_r = np.diff(bc[-21:]) / bc[-21:-1]
        var_b = float(np.var(bc_r))
        beta_20 = float(np.cov(tc_r, bc_r)[0, 1] / var_b) if var_b > 1e-10 else 1.0

    beta_adj_factor = 1.0 / max(beta_60, 0.3)

    # RS trend: improving, deteriorating, or flat
    rs5 = rs_results.get("rs_5d", 0)
    rs20 = rs_results.get("rs_20d", 0)
    if rs5 > rs20 + 1:
        rs_trend = "IMPROVING"
    elif rs5 < rs20 - 1:
        rs_trend = "DETERIORATING"
    else:
        rs_trend = "STABLE"

    # Classification
    avg_rs = (rs_results.get("rs_5d", 0) + rs_results.get("rs_20d", 0)) / 2
    if avg_rs > 3:
        rs_label = "STRONG_LEADER"
        rs_desc = f"Significantly outperforming {benchmark} — leadership"
    elif avg_rs > 1:
        rs_label = "OUTPERFORMING"
        rs_desc = f"Outperforming {benchmark} — relative strength confirmed"
    elif avg_rs > -1:
        rs_label = "IN_LINE"
        rs_desc = f"Performing in line with {benchmark}"
    elif avg_rs > -3:
        rs_label = "UNDERPERFORMING"
        rs_desc = f"Underperforming {benchmark} — relative weakness"
    else:
        rs_label = "STRONG_LAGGARD"
        rs_desc = f"Significantly underperforming {benchmark} — avoid long"

    return {
        "benchmark": benchmark,
        **rs_results,
        "rs_trend": rs_trend,
        "rs_label": rs_label,
        "rs_desc": rs_desc,
        "beta_60d": round(beta_60, 2),
        "beta_20d": round(beta_20, 2),
        "beta_adj_factor": round(beta_adj_factor, 2),
    }


def _compute_vwap(opens, highs, lows, closes, volumes, spot):
    """
    Compute rolling and anchored VWAP levels.

    Rolling VWAP: cumulative typical_price * volume / cumulative volume
    over fixed windows (5d, 20d). Tells you the volume-weighted fair value.

    VWAP bands: ±1σ and ±2σ around VWAP using volume-weighted variance.
    These act as dynamic support/resistance — institutional mean-reversion targets.

    Anchored VWAP: from the start of the current month (~20d) for a
    medium-term institutional reference level.
    """
    n = len(closes)
    if n < 5 or len(volumes) < 5:
        return _empty_vwap()

    typical = (highs + lows + closes) / 3
    vol_safe = np.where(volumes > 0, volumes, 1)

    result = {}

    for period, label in [(5, "vwap_5d"), (20, "vwap_20d")]:
        if n < period:
            result[label] = None
            continue

        tp_window = typical[-period:]
        vol_window = vol_safe[-period:]

        cum_tpv = float(np.sum(tp_window * vol_window))
        cum_vol = float(np.sum(vol_window))

        if cum_vol <= 0:
            result[label] = None
            continue

        vwap = cum_tpv / cum_vol

        # VWAP bands: volume-weighted standard deviation
        # variance = Σ(vol * (tp - vwap)²) / Σ(vol)
        variance = float(np.sum(vol_window * (tp_window - vwap) ** 2) / cum_vol)
        std = float(np.sqrt(variance))

        distance_pct = (spot - vwap) / vwap * 100 if vwap > 0 else 0

        result[label] = {
            "value": round(vwap, 2),
            "upper_1": round(vwap + std, 2),
            "lower_1": round(vwap - std, 2),
            "upper_2": round(vwap + 2 * std, 2),
            "lower_2": round(vwap - 2 * std, 2),
            "std": round(std, 2),
            "distance_pct": round(distance_pct, 2),
            "position": "ABOVE" if spot > vwap else "BELOW",
        }

    # Anchored VWAP from the monthly window (approximate current month)
    anchor_period = min(20, n)
    tp_anchor = typical[-anchor_period:]
    vol_anchor = vol_safe[-anchor_period:]
    cum_tpv_a = float(np.sum(tp_anchor * vol_anchor))
    cum_vol_a = float(np.sum(vol_anchor))
    anchored = cum_tpv_a / cum_vol_a if cum_vol_a > 0 else float(spot)

    result["anchored_monthly"] = round(anchored, 2)

    # Overall VWAP context
    vwap20 = result.get("vwap_20d")
    if vwap20:
        dist = vwap20["distance_pct"]
        if dist > 2:
            vwap_context = "EXTENDED_ABOVE"
            vwap_desc = f"Price {dist:+.1f}% above 20d VWAP — extended, potential reversion target below"
        elif dist > 0.5:
            vwap_context = "ABOVE"
            vwap_desc = f"Price above 20d VWAP — institutional buyers in control"
        elif dist > -0.5:
            vwap_context = "AT_VWAP"
            vwap_desc = "Price near 20d VWAP — at fair value, key decision level"
        elif dist > -2:
            vwap_context = "BELOW"
            vwap_desc = f"Price below 20d VWAP — sellers in control, watch for reclaim"
        else:
            vwap_context = "EXTENDED_BELOW"
            vwap_desc = f"Price {dist:+.1f}% below 20d VWAP — extended, potential reversion target above"
    else:
        vwap_context = "N/A"
        vwap_desc = ""

    result["context"] = vwap_context
    result["context_desc"] = vwap_desc

    return result


def _empty_vwap():
    return {
        "vwap_5d": None,
        "vwap_20d": None,
        "anchored_monthly": 0,
        "context": "N/A",
        "context_desc": "Insufficient data",
    }


def _classify_trend(ma_data, atr_data, rs_data, spot):
    """Synthesize MAs, ATR, and RS into an overall trend assessment."""
    alignment = ma_data.get("alignment", 0)
    atr_pct = atr_data.get("atr_pct", 0)
    rs_label = rs_data.get("rs_label", "IN_LINE")

    # Trend strength: combines MA alignment and RS
    if alignment >= 2 and rs_label in ("STRONG_LEADER", "OUTPERFORMING"):
        trend_label = "STRONG_UPTREND"
        trend_desc = "Bullish structure confirmed by relative strength"
        trend_score = 2
    elif alignment >= 2:
        trend_label = "UPTREND"
        trend_desc = "Above major MAs — uptrend structure intact"
        trend_score = 1
    elif alignment <= -2 and rs_label in ("STRONG_LAGGARD", "UNDERPERFORMING"):
        trend_label = "STRONG_DOWNTREND"
        trend_desc = "Bearish structure confirmed by relative weakness"
        trend_score = -2
    elif alignment <= -2:
        trend_label = "DOWNTREND"
        trend_desc = "Below major MAs — downtrend structure"
        trend_score = -1
    elif abs(alignment) <= 1:
        trend_label = "RANGEBOUND"
        trend_desc = "Mixed MAs — no clear trend, likely range-bound"
        trend_score = 0
    else:
        trend_label = "TRANSITIONAL"
        trend_desc = "Trend is shifting — watch for confirmation"
        trend_score = 0

    # Volatility context from ATR
    if atr_pct > 3:
        vol_label = "HIGH_VOL"
        vol_desc = f"ATR {atr_pct:.1f}% — wide daily ranges, use wider stops"
    elif atr_pct > 1.5:
        vol_label = "NORMAL_VOL"
        vol_desc = f"ATR {atr_pct:.1f}% — typical daily ranges"
    else:
        vol_label = "LOW_VOL"
        vol_desc = f"ATR {atr_pct:.1f}% — compressed ranges, potential squeeze"

    # Directional confidence modifier based on technicals
    if trend_score >= 2:
        tech_bias = "BULLISH"
    elif trend_score >= 1:
        tech_bias = "LEAN_BULLISH"
    elif trend_score <= -2:
        tech_bias = "BEARISH"
    elif trend_score <= -1:
        tech_bias = "LEAN_BEARISH"
    else:
        tech_bias = "NEUTRAL"

    return {
        "trend_label": trend_label,
        "trend_desc": trend_desc,
        "trend_score": trend_score,
        "vol_label": vol_label,
        "vol_desc": vol_desc,
        "tech_bias": tech_bias,
    }


def _empty_technicals():
    return {
        "moving_averages": {
            "sma_20": None, "sma_50": None, "sma_200": None,
            "alignment": 0, "alignment_label": "UNKNOWN",
            "alignment_desc": "Insufficient data",
            "alignment_details": [], "cross": None,
        },
        "atr": {
            "atr": 0, "atr_pct": 0, "period": 14,
            "recent_range": 0, "recent_range_pct": 0,
            "atr_trend": "UNKNOWN", "atr_change_pct": 0,
        },
        "relative_strength": _empty_rs("SPY"),
        "vwap": _empty_vwap(),
        "trend": {
            "trend_label": "UNKNOWN", "trend_desc": "Insufficient data",
            "trend_score": 0, "vol_label": "UNKNOWN", "vol_desc": "",
            "tech_bias": "NEUTRAL",
        },
    }


def _empty_rs(benchmark):
    return {
        "benchmark": benchmark,
        "rs_5d": 0, "rs_20d": 0, "rs_60d": 0,
        "rs_trend": "UNKNOWN",
        "rs_label": "UNKNOWN",
        "rs_desc": "Could not compute relative strength",
        "beta_60d": 1.0,
        "beta_20d": 1.0,
        "beta_adj_factor": 1.0,
    }
