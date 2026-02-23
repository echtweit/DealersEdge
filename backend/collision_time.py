"""
Collision Time Calculator — Kanazawa kinetic theory adaptation.
Estimates expected trading days for price to reach key dealer levels using
first-passage-time theory: T = L² / (2σ²).

Regime adjustments: turbulent regimes accelerate arrival, dampened regimes slow it.
"""

import numpy as np
from scipy.special import erfc


def compute_collision_times(
    spot: float,
    levels: dict,
    atr_dollar: float,
    acf_regime: str,
    reynolds_regime: str,
    dte: int,
) -> list[dict]:
    if atr_dollar <= 0 or spot <= 0:
        return []

    sigma = atr_dollar
    sigma_sq = sigma ** 2

    regime_mult = 1.0
    if reynolds_regime == "TURBULENT":
        regime_mult = 0.6
    elif reynolds_regime == "TRANSITIONAL":
        regime_mult = 0.8
    elif acf_regime == "LONG_GAMMA":
        regime_mult = 1.4

    results = []
    for label, price in levels.items():
        if not price or price <= 0:
            continue
        price = float(price)

        distance = abs(spot - price)
        if distance < 0.01:
            t_raw = 0.0
        else:
            t_raw = (distance ** 2) / (2 * sigma_sq)

        t_adjusted = t_raw * regime_mult

        if dte > 0 and sigma > 0 and distance > 0:
            z = distance / (sigma * np.sqrt(2 * max(dte, 1)))
            p_within_dte = float(erfc(z))
        else:
            p_within_dte = 0.95 if distance < 0.01 else 0.0

        p_within_dte = min(0.99, max(0.01, p_within_dte))

        if t_adjusted < 1:
            urgency = "NOW"
        elif t_adjusted < 2:
            urgency = "IMMINENT"
        elif t_adjusted <= dte:
            urgency = "SOON"
        elif t_adjusted > dte * 2:
            urgency = "UNLIKELY"
        else:
            urgency = "POSSIBLE"

        results.append({
            "level_label": label,
            "level_price": round(price, 2),
            "distance": round(distance, 2),
            "distance_pct": round(distance / spot * 100, 2),
            "expected_days_raw": round(t_raw, 1),
            "expected_days_adj": round(t_adjusted, 1),
            "regime_mult": regime_mult,
            "prob_within_dte": round(p_within_dte * 100, 1),
            "urgency": urgency,
            "side": "above" if price > spot else "below",
        })

    results.sort(key=lambda r: r["expected_days_adj"])
    return results
