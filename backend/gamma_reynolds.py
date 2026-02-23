"""
Gamma Reynolds Number — ported from OscillationPriming.
Measures speculative call flow vs dealer inventory capacity.

Re < 0.7 : LAMINAR  — dealers absorb, walls hold, mean-reversion
Re 0.7-1 : TRANSITIONAL — dealers straining, could go either way
Re > 1.0 : TURBULENT — dealers overwhelmed, walls become accelerators
"""

import numpy as np


def compute_gamma_reynolds(
    calls: list[dict],
    puts: list[dict],
    spot: float,
) -> dict:
    if not calls or not puts or spot <= 0:
        return {
            "reynolds_number": 0, "regime": "UNKNOWN",
            "speculative_gamma": 0, "dealer_gamma": 0,
            "call_put_ratio": 0, "call_volume": 0, "put_volume": 0,
            "call_oi": 0, "put_oi": 0, "atm_iv": 0,
        }

    atm_tolerance = 0.10
    call_volume = 0
    put_volume = 0
    call_oi = 0
    put_oi = 0
    speculative_gamma = 0.0
    dealer_gamma = 0.0
    atm_ivs = []

    for c in calls:
        strike = float(c.get("strike", 0))
        vol = int(c.get("volume", 0) or 0)
        oi = int(c.get("openInterest", 0) or 0)
        iv = float(c.get("impliedVolatility", 0.3) or 0.3)
        call_volume += vol
        call_oi += oi

        if strike <= 0 or iv <= 0:
            continue

        moneyness = spot / strike
        gamma_proxy = np.exp(-0.5 * ((np.log(moneyness)) / max(iv, 0.01)) ** 2)

        # Speculative gamma: near-ATM call volume (retail buying)
        if abs(strike - spot) / spot <= atm_tolerance:
            speculative_gamma += gamma_proxy * vol * 100 * spot * spot * 0.01
            if iv > 0:
                atm_ivs.append(iv)

        # Dealer gamma from OI (dealers typically net short calls → long gamma)
        dealer_gamma += gamma_proxy * oi * 100 * spot * spot * 0.01

    for p in puts:
        strike = float(p.get("strike", 0))
        vol = int(p.get("volume", 0) or 0)
        oi = int(p.get("openInterest", 0) or 0)
        iv = float(p.get("impliedVolatility", 0.3) or 0.3)
        put_volume += vol
        put_oi += oi

        if strike <= 0 or iv <= 0:
            continue

        moneyness = spot / strike
        gamma_proxy = np.exp(-0.5 * ((np.log(moneyness)) / max(iv, 0.01)) ** 2)
        dealer_gamma -= gamma_proxy * oi * 100 * spot * spot * 0.01

    if abs(dealer_gamma) < 1e-6:
        re_gamma = float("inf") if speculative_gamma > 0 else 0.0
    else:
        re_gamma = abs(speculative_gamma / dealer_gamma)

    if re_gamma > 1.0:
        regime = "TURBULENT"
    elif re_gamma > 0.7:
        regime = "TRANSITIONAL"
    else:
        regime = "LAMINAR"

    cp_ratio = call_volume / max(put_volume, 1)
    atm_iv = float(np.mean(atm_ivs)) if atm_ivs else 0.0

    return {
        "reynolds_number": round(float(min(re_gamma, 99)), 4),
        "speculative_gamma": round(float(speculative_gamma), 2),
        "dealer_gamma": round(float(dealer_gamma), 2),
        "regime": regime,
        "call_put_ratio": round(float(cp_ratio), 2),
        "call_volume": int(call_volume),
        "put_volume": int(put_volume),
        "call_oi": int(call_oi),
        "put_oi": int(put_oi),
        "atm_iv": round(float(atm_iv), 4),
    }


def detect_phase_transition(
    acf_daily_results: list[dict],
    window: int = 20,
    threshold_pct: float = 12.9,
) -> dict:
    if not acf_daily_results:
        return {"regime": "UNKNOWN", "pct_amplified": 0}

    recent = acf_daily_results[-window:] if len(acf_daily_results) >= window else acf_daily_results
    lag1_values = [d["lag1_acf"] for d in recent]
    n_amplified = sum(1 for v in lag1_values if v > 0.05)
    pct_amplified = 100 * n_amplified / len(recent)
    distance = threshold_pct - pct_amplified

    if pct_amplified > threshold_pct:
        regime = "TURBULENT"
        warning = "ABOVE critical threshold — phase transition likely"
    elif pct_amplified > threshold_pct * 0.7:
        regime = "APPROACHING"
        warning = "Approaching critical threshold — elevated risk"
    else:
        regime = "LAMINAR"
        warning = None

    return {
        "pct_amplified": round(pct_amplified, 1),
        "distance_to_transition": round(distance, 1),
        "regime": regime,
        "warning": warning,
        "n_amplified_days": n_amplified,
        "window": len(recent),
    }
