"""
Volatility Analysis — IV vs Realized Vol, Term Structure, and Put/Call Skew.

Three questions every option buyer must answer before entry:
  1. Am I overpaying? (IV/HV ratio)
  2. Is short-dated premium bloated? (Term structure)
  3. Where is the fear/greed? (Skew)
"""

import numpy as np
from scipy.stats import norm


def compute_vol_analysis(
    calls: list[dict],
    puts: list[dict],
    spot: float,
    dte: int,
    price_history: list[dict],
    atm_iv: float = 0,
    multi_exp_chains: list[dict] = None,
    gex_regime: str = "POSITIVE_GAMMA",
    total_gex: float = 0,
    reynolds_regime: str = "LAMINAR",
) -> dict:
    closes = [bar["close"] for bar in price_history if bar.get("close", 0) > 0]

    iv_hv = _compute_iv_vs_hv(atm_iv, closes, dte)
    skew = _compute_skew(calls, puts, spot, dte)
    term = _compute_term_structure(multi_exp_chains, spot) if multi_exp_chains else _empty_term()
    vrp = _compute_vrp(atm_iv, closes, dte, gex_regime, total_gex, reynolds_regime)
    edge = _compute_vol_edge(iv_hv, skew, term, dte, vrp)

    return {
        "iv_hv": iv_hv,
        "skew": skew,
        "term_structure": term,
        "vrp": vrp,
        "vol_edge": edge,
    }


# ── IV vs Realized Volatility ──────────────────────────────────────────────


def _compute_iv_vs_hv(atm_iv: float, closes: list, dte: int) -> dict:
    if atm_iv <= 0:
        atm_iv = 0.3

    n = len(closes)
    if n < 20:
        return {
            "atm_iv": round(atm_iv * 100, 1),
            "hv_10d": 0, "hv_20d": 0, "hv_30d": 0, "hv_60d": 0,
            "iv_hv_ratio": 1.0, "hv_used": 0,
            "iv_percentile_proxy": 50,
            "context": "N/A", "label": "Insufficient data",
        }

    log_ret = np.diff(np.log(closes))
    ann = np.sqrt(252)

    hv = {}
    for window, label in [(10, "hv_10d"), (20, "hv_20d"), (30, "hv_30d"), (60, "hv_60d")]:
        if len(log_ret) >= window:
            hv[label] = round(float(np.std(log_ret[-window:]) * ann) * 100, 1)
        else:
            hv[label] = 0

    # Use the window closest to DTE for the primary ratio
    if dte <= 10:
        hv_compare = hv["hv_10d"] or hv["hv_20d"]
    elif dte <= 20:
        hv_compare = hv["hv_20d"] or hv["hv_10d"]
    elif dte <= 30:
        hv_compare = hv["hv_30d"] or hv["hv_20d"]
    else:
        hv_compare = hv["hv_60d"] or hv["hv_30d"]

    iv_pct = atm_iv * 100
    ratio = iv_pct / hv_compare if hv_compare > 1 else 1.0

    # HV percentile proxy: where is current HV relative to the full history?
    hv_percentile = 50
    if len(log_ret) >= 60:
        rolling_hv = []
        for i in range(20, len(log_ret)):
            w = log_ret[i - 20 : i]
            rolling_hv.append(float(np.std(w) * ann) * 100)
        if rolling_hv:
            current_hv = rolling_hv[-1]
            hv_percentile = int(round(
                100 * sum(1 for h in rolling_hv if h <= current_hv) / len(rolling_hv)
            ))

    if ratio < 0.80:
        context = "CHEAP"
        label = f"IV {ratio:.0%} of realized — options are cheap, good for buying"
    elif ratio < 0.95:
        context = "SLIGHT_DISCOUNT"
        label = f"IV slightly below realized — fair entry for buyers"
    elif ratio < 1.10:
        context = "FAIR"
        label = f"IV in line with realized — no vol edge either way"
    elif ratio < 1.30:
        context = "SLIGHT_PREMIUM"
        label = f"IV slightly above realized — acceptable but not ideal"
    elif ratio < 1.60:
        context = "EXPENSIVE"
        label = f"IV {ratio:.0%} of realized — options are expensive, consider spreads"
    else:
        context = "VERY_EXPENSIVE"
        label = f"IV {ratio:.0%} of realized — extremely overpriced, avoid naked longs"

    return {
        "atm_iv": round(iv_pct, 1),
        **hv,
        "hv_used": hv_compare,
        "hv_window": f"{dte}d-matched",
        "iv_hv_ratio": round(ratio, 2),
        "iv_percentile_proxy": hv_percentile,
        "context": context,
        "label": label,
    }


# ── Put/Call IV Skew ────────────────────────────────────────────────────────


def _compute_skew(
    calls: list[dict], puts: list[dict], spot: float, dte: int,
) -> dict:
    T = max(dte / 365.0, 0.001)

    call_ivs = []
    put_ivs = []
    atm_call_iv = 0
    atm_put_iv = 0
    min_call_dist = float("inf")
    min_put_dist = float("inf")

    for c in calls:
        strike = float(c.get("strike", 0))
        iv = float(c.get("impliedVolatility", 0) or 0)
        if strike <= 0 or iv <= 0:
            continue
        moneyness = strike / spot
        call_ivs.append((moneyness, iv, strike))
        dist = abs(moneyness - 1.0)
        if dist < min_call_dist:
            min_call_dist = dist
            atm_call_iv = iv

    for p in puts:
        strike = float(p.get("strike", 0))
        iv = float(p.get("impliedVolatility", 0) or 0)
        if strike <= 0 or iv <= 0:
            continue
        moneyness = strike / spot
        put_ivs.append((moneyness, iv, strike))
        dist = abs(moneyness - 1.0)
        if dist < min_put_dist:
            min_put_dist = dist
            atm_put_iv = iv

    if not call_ivs or not put_ivs:
        return _empty_skew()

    # 25-delta approximation: ~5% OTM for short DTE, ~8% for longer
    otm_target = 0.05 if dte <= 14 else 0.08

    # OTM put IV (~25 delta put = strike below spot)
    otm_put_iv = _find_otm_iv(put_ivs, 1.0 - otm_target, side="below")
    # OTM call IV (~25 delta call = strike above spot)
    otm_call_iv = _find_otm_iv(call_ivs, 1.0 + otm_target, side="above")

    atm_iv_avg = (atm_call_iv + atm_put_iv) / 2 if atm_call_iv > 0 and atm_put_iv > 0 else max(atm_call_iv, atm_put_iv)

    # Skew = OTM put IV - OTM call IV (positive = put skew / fear)
    skew_raw = (otm_put_iv - otm_call_iv) * 100 if otm_put_iv > 0 and otm_call_iv > 0 else 0
    # Normalized: skew relative to ATM IV
    skew_norm = skew_raw / (atm_iv_avg * 100) if atm_iv_avg > 0 else 0

    # Risk reversal: difference in IV between equal-distance OTM
    risk_reversal = round(skew_raw, 1)

    if skew_norm > 0.15:
        skew_regime = "HIGH_PUT_SKEW"
        skew_desc = "Heavy put hedging demand — puts are expensive, calls are relatively cheap"
        skew_trade = "If bearish: puts are pricey, consider put spreads. If bullish: calls have vol edge."
    elif skew_norm > 0.05:
        skew_regime = "MODERATE_PUT_SKEW"
        skew_desc = "Normal put skew — standard hedging premium"
        skew_trade = "Typical skew — no strong vol edge from skew alone"
    elif skew_norm > -0.05:
        skew_regime = "FLAT"
        skew_desc = "Flat skew — unusual, watch for complacency"
        skew_trade = "Flat skew can precede volatility expansion — protective puts are cheap"
    elif skew_norm > -0.15:
        skew_regime = "CALL_SKEW"
        skew_desc = "Call skew — speculative call buying inflating upside IV"
        skew_trade = "If bullish: calls are expensive, consider call spreads. Puts are relatively cheap."
    else:
        skew_regime = "EXTREME_CALL_SKEW"
        skew_desc = "Extreme call skew — euphoria/squeeze pricing on upside"
        skew_trade = "Calls overpriced. If bullish, use debit spreads. Puts are a steal for protection."

    return {
        "otm_put_iv": round(otm_put_iv * 100, 1),
        "otm_call_iv": round(otm_call_iv * 100, 1),
        "atm_iv": round(atm_iv_avg * 100, 1),
        "risk_reversal": risk_reversal,
        "skew_norm": round(skew_norm, 3),
        "regime": skew_regime,
        "description": skew_desc,
        "trade_implication": skew_trade,
    }


def _find_otm_iv(iv_list, target_moneyness, side="below"):
    best = None
    best_dist = float("inf")
    for moneyness, iv, strike in iv_list:
        if side == "below" and moneyness > target_moneyness + 0.01:
            continue
        if side == "above" and moneyness < target_moneyness - 0.01:
            continue
        dist = abs(moneyness - target_moneyness)
        if dist < best_dist:
            best_dist = dist
            best = iv
    return best if best else 0


def _empty_skew():
    return {
        "otm_put_iv": 0, "otm_call_iv": 0, "atm_iv": 0,
        "risk_reversal": 0, "skew_norm": 0,
        "regime": "UNKNOWN", "description": "Insufficient data",
        "trade_implication": "",
    }


# ── Term Structure ──────────────────────────────────────────────────────────


def _compute_term_structure(multi_exp_chains: list[dict], spot: float) -> dict:
    """
    Compute ATM IV across multiple expirations to determine term structure shape.
    Each entry: {"dte": int, "calls": [...], "puts": [...]}
    """
    points = []

    for entry in multi_exp_chains:
        exp_dte = entry.get("dte", 0)
        calls = entry.get("calls", [])
        puts = entry.get("puts", [])
        if exp_dte <= 0 or (not calls and not puts):
            continue

        atm_iv = _extract_atm_iv(calls, puts, spot)
        if atm_iv > 0:
            points.append({
                "dte": exp_dte,
                "atm_iv": round(atm_iv * 100, 1),
                "expiration": entry.get("expiration", ""),
            })

    if len(points) < 2:
        return _empty_term()

    points.sort(key=lambda p: p["dte"])

    # Slope: compare shortest vs longest
    front_iv = points[0]["atm_iv"]
    back_iv = points[-1]["atm_iv"]
    slope = back_iv - front_iv
    slope_pct = (slope / front_iv * 100) if front_iv > 0 else 0

    if slope > 2 and slope_pct > 5:
        shape = "CONTANGO"
        desc = "Normal upward slope — short-dated options are cheaper (good for buying near-term)"
    elif slope > 0.5:
        shape = "MILD_CONTANGO"
        desc = "Slight upward slope — roughly normal term structure"
    elif slope > -0.5:
        shape = "FLAT"
        desc = "Flat term structure — no DTE preference from vol standpoint"
    elif slope > -2:
        shape = "MILD_BACKWARDATION"
        desc = "Slight inversion — near-term premium slightly elevated"
    else:
        shape = "BACKWARDATION"
        desc = "Inverted — near-term IV higher than back-month (event risk priced in, short-dated expensive)"

    front_back_ratio = round(front_iv / back_iv, 2) if back_iv > 0 else 1.0

    if shape in ("BACKWARDATION", "MILD_BACKWARDATION"):
        trade_impl = "Short-dated options are expensive — consider buying further out or using spreads to offset decay"
    elif shape in ("CONTANGO",):
        trade_impl = "Short-dated options are cheap relative to longer-dated — good environment for near-term directional plays"
    else:
        trade_impl = "No strong term structure signal — choose DTE based on thesis conviction and ATR coverage"

    return {
        "points": points,
        "shape": shape,
        "description": desc,
        "trade_implication": trade_impl,
        "front_iv": front_iv,
        "back_iv": back_iv,
        "slope": round(slope, 1),
        "slope_pct": round(slope_pct, 1),
        "front_back_ratio": front_back_ratio,
    }


def _extract_atm_iv(calls, puts, spot):
    best_iv = 0
    best_dist = float("inf")
    for opt in calls + puts:
        strike = float(opt.get("strike", 0))
        iv = float(opt.get("impliedVolatility", 0) or 0)
        if strike <= 0 or iv <= 0:
            continue
        dist = abs(strike - spot)
        if dist < best_dist:
            best_dist = dist
            best_iv = iv
    return best_iv


def _empty_term():
    return {
        "points": [], "shape": "UNKNOWN",
        "description": "Insufficient data", "trade_implication": "",
        "front_iv": 0, "back_iv": 0, "slope": 0, "slope_pct": 0,
        "front_back_ratio": 1.0,
    }


# ── Variance Risk Premium (VRP) + GEX-Implied Vol ──────────────────────────


def _compute_vrp(
    atm_iv: float, closes: list, dte: int,
    gex_regime: str, total_gex: float, reynolds_regime: str,
) -> dict:
    """
    Variance Risk Premium: the structural premium option buyers pay over
    fair value. VRP = IV² - HV² (annualized variance terms).

    GEX-Implied Vol Adjustment (from Deep Hedging / Bakshi):
    When dealers are long gamma (positive GEX), they dampen realized vol
    by ~15-25% via mechanical hedging. When short gamma (negative GEX),
    they amplify realized vol by ~10-15%. This creates a GEX-adjusted
    expected realized vol that gives a more accurate VRP estimate.
    """
    if atm_iv <= 0:
        atm_iv = 0.3

    n = len(closes)
    if n < 20:
        return _empty_vrp()

    log_ret = np.diff(np.log(closes))
    ann = np.sqrt(252)
    hv_20 = float(np.std(log_ret[-20:]) * ann) if len(log_ret) >= 20 else 0

    if hv_20 <= 0.01:
        return _empty_vrp()

    iv_var = atm_iv ** 2
    hv_var = hv_20 ** 2
    vrp_raw = (iv_var - hv_var) * 100  # in variance points × 100

    # GEX-implied vol adjustment:
    # Positive gamma → dealers absorb moves → realized vol suppressed
    # Negative gamma → dealers amplify moves → realized vol elevated
    # The asymmetry: dampening is ~2.5x stronger than amplification
    if gex_regime == "POSITIVE_GAMMA":
        if reynolds_regime == "LAMINAR":
            gex_vol_mult = 0.78  # strong dampening: 22% vol suppression
        else:
            gex_vol_mult = 0.88  # transitional: dampening weakening
    else:
        if reynolds_regime == "TURBULENT":
            gex_vol_mult = 1.15  # strong amplification
        else:
            gex_vol_mult = 1.08  # mild amplification

    gex_implied_hv = hv_20 * gex_vol_mult
    gex_adjusted_vrp = (iv_var - (gex_implied_hv ** 2)) * 100

    # VRP context: positive = you're overpaying, negative = cheap
    if gex_adjusted_vrp > 5:
        vrp_context = "HIGH_PREMIUM"
        vrp_label = "Large VRP headwind — you're paying significantly above expected vol"
    elif gex_adjusted_vrp > 2:
        vrp_context = "MODERATE_PREMIUM"
        vrp_label = "Moderate VRP — options cost more than expected delivery"
    elif gex_adjusted_vrp > 0:
        vrp_context = "SMALL_PREMIUM"
        vrp_label = "Small VRP — near fair value"
    elif gex_adjusted_vrp > -2:
        vrp_context = "FAIR"
        vrp_label = "VRP near zero — options fairly priced given GEX regime"
    else:
        vrp_context = "DISCOUNT"
        vrp_label = "Negative VRP — options are cheap vs expected delivery"

    # Daily VRP drag: normalize to a standard holding period
    days = max(dte, 5)
    daily_vrp_drag = gex_adjusted_vrp / days

    return {
        "vrp_raw": round(vrp_raw, 2),
        "vrp_gex_adjusted": round(gex_adjusted_vrp, 2),
        "gex_implied_hv": round(gex_implied_hv * 100, 1),
        "gex_vol_mult": round(gex_vol_mult, 2),
        "hv_20d": round(hv_20 * 100, 1),
        "atm_iv": round(atm_iv * 100, 1),
        "daily_vrp_drag": round(daily_vrp_drag, 3),
        "context": vrp_context,
        "label": vrp_label,
    }


def _empty_vrp():
    return {
        "vrp_raw": 0, "vrp_gex_adjusted": 0,
        "gex_implied_hv": 0, "gex_vol_mult": 1.0,
        "hv_20d": 0, "atm_iv": 0, "daily_vrp_drag": 0,
        "context": "N/A", "label": "Insufficient data",
    }


# ── Vol Edge Synthesis ──────────────────────────────────────────────────────


def _compute_vol_edge(iv_hv: dict, skew: dict, term: dict, dte: int, vrp: dict = None) -> dict:
    """
    Synthesize IV/HV, skew, and term structure into a single vol edge assessment.
    """
    ratio = iv_hv.get("iv_hv_ratio", 1.0)
    iv_ctx = iv_hv.get("context", "FAIR")
    skew_regime = skew.get("regime", "UNKNOWN")
    term_shape = term.get("shape", "UNKNOWN")

    score = 0
    factors = []

    # IV/HV is the primary driver (weight: 40%)
    if iv_ctx in ("CHEAP", "SLIGHT_DISCOUNT"):
        score += 40 if iv_ctx == "CHEAP" else 30
        factors.append(f"IV is {ratio:.0%} of realized vol — cheap")
    elif iv_ctx == "FAIR":
        score += 20
        factors.append("IV roughly matches realized vol")
    elif iv_ctx == "SLIGHT_PREMIUM":
        score += 10
        factors.append(f"IV slightly above realized ({ratio:.0%}) — acceptable")
    elif iv_ctx == "EXPENSIVE":
        score += 0
        factors.append(f"IV {ratio:.0%} of realized — expensive, consider spreads")
    else:
        score -= 10
        factors.append(f"IV {ratio:.0%} of realized — very overpriced")

    # Term structure (weight: 30%)
    if term_shape in ("CONTANGO",):
        score += 25
        factors.append("Term structure normal — short-dated options cheaper")
    elif term_shape == "MILD_CONTANGO":
        score += 15
        factors.append("Mild contango — no term structure headwind")
    elif term_shape == "FLAT":
        score += 10
    elif term_shape == "MILD_BACKWARDATION":
        score += 0
        factors.append("Slight inversion — near-term premium elevated")
    elif term_shape == "BACKWARDATION":
        score -= 10
        factors.append("Term structure inverted — you're paying extra for short-dated")

    # Skew (weight: 20%)
    if skew_regime == "HIGH_PUT_SKEW":
        score += 5
        factors.append("High put skew — calls relatively cheap (if bullish)")
    elif skew_regime == "CALL_SKEW":
        factors.append("Call skew — calls expensive (if bullish, use spreads)")
    elif skew_regime == "EXTREME_CALL_SKEW":
        score -= 5
        factors.append("Extreme call skew — euphoric pricing")
    elif skew_regime == "FLAT":
        score += 10
        factors.append("Flat skew — puts are cheap for protection")

    # HV percentile (weight: 10%)
    hv_pctl = iv_hv.get("iv_percentile_proxy", 50)
    if hv_pctl < 25:
        score += 5
        factors.append("Realized vol is low — potential for expansion")
    elif hv_pctl > 80:
        factors.append("Realized vol already elevated — may be peaking")

    # VRP adjustment (weight: 15%) — from Bakshi "Dark Matter" paper
    if vrp and vrp.get("context") != "N/A":
        vrp_adj = vrp.get("vrp_gex_adjusted", 0)
        vrp_ctx = vrp.get("context", "FAIR")
        if vrp_ctx == "DISCOUNT":
            score += 15
            factors.append(f"Negative VRP ({vrp_adj:+.1f}) — options are cheap vs GEX-implied vol")
        elif vrp_ctx == "FAIR":
            score += 8
            factors.append("VRP near zero — fairly priced given dealer positioning")
        elif vrp_ctx == "SMALL_PREMIUM":
            score += 3
        elif vrp_ctx == "MODERATE_PREMIUM":
            score -= 5
            factors.append(f"Moderate VRP headwind ({vrp_adj:+.1f}) — paying above expected delivery")
        elif vrp_ctx == "HIGH_PREMIUM":
            score -= 10
            factors.append(f"Large VRP headwind ({vrp_adj:+.1f}) — structurally overpaying for vol")

    score = max(0, min(100, score))

    if score >= 60:
        verdict = "STRONG_BUY_VOL"
        trade_label = "Options are cheap — strong environment for buying premium"
    elif score >= 40:
        verdict = "BUY_VOL"
        trade_label = "Options are fairly priced — directional plays viable"
    elif score >= 25:
        verdict = "NEUTRAL_VOL"
        trade_label = "No strong vol edge — use spreads to reduce cost basis"
    elif score >= 10:
        verdict = "EXPENSIVE_VOL"
        trade_label = "Options are expensive — strongly prefer debit spreads over naked longs"
    else:
        verdict = "AVOID_BUYING"
        trade_label = "Premium is very overpriced — avoid buying naked options"

    return {
        "score": score,
        "verdict": verdict,
        "label": trade_label,
        "factors": factors,
    }
