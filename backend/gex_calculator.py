"""
Gamma Exposure (GEX) calculator.
Computes dealer gamma exposure per strike from options chain data,
identifies the GEX flip point, absolute gamma strike, and regime.
"""
import numpy as np
from scipy.stats import norm
from typing import Optional


SHARES_PER_CONTRACT = 100


def black_scholes_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Calculate BS gamma for a single option. Same for calls and puts."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    return norm.pdf(d1) / (S * sigma * np.sqrt(T))


def black_scholes_delta(S: float, K: float, T: float, r: float, sigma: float, option_type: str) -> float:
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    if option_type == "call":
        return norm.cdf(d1)
    return norm.cdf(d1) - 1


def black_scholes_charm(S: float, K: float, T: float, r: float, sigma: float, option_type: str) -> float:
    """dDelta/dTime — measures how delta decays over time."""
    if T <= 0.001 or sigma <= 0 or S <= 0:
        return 0.0
    sqrt_T = np.sqrt(T)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T

    charm_base = -norm.pdf(d1) * (2 * r * T - d2 * sigma * sqrt_T) / (2 * T * sigma * sqrt_T)
    if option_type == "put":
        charm_base += r * np.exp(-r * T) * norm.cdf(-d2)
    else:
        charm_base -= r * np.exp(-r * T) * norm.cdf(d2)
    return charm_base


def black_scholes_vanna(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """dDelta/dVol — measures how delta changes with IV."""
    if T <= 0.001 or sigma <= 0 or S <= 0:
        return 0.0
    sqrt_T = np.sqrt(T)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return -norm.pdf(d1) * d2 / sigma


def calculate_gex_profile(
    calls: list[dict],
    puts: list[dict],
    spot: float,
    dte: int,
    risk_free_rate: float = 0.05,
) -> dict:
    """
    Calculate GEX profile across all strikes.
    Dealers are assumed net short options (sold to public).
    Call GEX: positive (dealers long delta hedge → sell on rise)
    Put GEX: negative sign flipped (dealers short delta hedge → buy on drop)

    Returns per-strike GEX, flip point, absolute gamma strike, and totals.
    """
    T = max(dte / 365.0, 0.001)
    strikes = set()
    call_map = {}
    put_map = {}

    for c in calls:
        k = float(c.get("strike", 0))
        if k <= 0:
            continue
        strikes.add(k)
        call_map[k] = c

    for p in puts:
        k = float(p.get("strike", 0))
        if k <= 0:
            continue
        strikes.add(k)
        put_map[k] = p

    strikes = sorted(strikes)
    gex_by_strike = []
    total_call_gex = 0.0
    total_put_gex = 0.0

    for k in strikes:
        call_oi = int(call_map.get(k, {}).get("openInterest", 0))
        put_oi = int(put_map.get(k, {}).get("openInterest", 0))

        call_iv = float(call_map.get(k, {}).get("impliedVolatility", 0.3))
        put_iv = float(put_map.get(k, {}).get("impliedVolatility", 0.3))

        call_iv = max(call_iv, 0.01)
        put_iv = max(put_iv, 0.01)

        call_gamma = black_scholes_gamma(spot, k, T, risk_free_rate, call_iv)
        put_gamma = black_scholes_gamma(spot, k, T, risk_free_rate, put_iv)

        # Dealer is net short → call GEX positive, put GEX negative
        call_gex = call_gamma * call_oi * SHARES_PER_CONTRACT * spot
        put_gex = -put_gamma * put_oi * SHARES_PER_CONTRACT * spot

        call_delta = black_scholes_delta(spot, k, T, risk_free_rate, call_iv, "call")
        put_delta = black_scholes_delta(spot, k, T, risk_free_rate, put_iv, "put")

        call_charm_val = black_scholes_charm(spot, k, T, risk_free_rate, call_iv, "call")
        put_charm_val = black_scholes_charm(spot, k, T, risk_free_rate, put_iv, "put")

        call_vanna_val = black_scholes_vanna(spot, k, T, risk_free_rate, call_iv)
        put_vanna_val = black_scholes_vanna(spot, k, T, risk_free_rate, put_iv)

        net_gex = call_gex + put_gex
        total_call_gex += call_gex
        total_put_gex += put_gex

        gex_by_strike.append({
            "strike": k,
            "call_oi": call_oi,
            "put_oi": put_oi,
            "call_gex": round(call_gex, 2),
            "put_gex": round(put_gex, 2),
            "net_gex": round(net_gex, 2),
            "call_delta": round(call_delta, 4),
            "put_delta": round(put_delta, 4),
            "net_dealer_delta": round(-(call_delta * call_oi + put_delta * put_oi) * SHARES_PER_CONTRACT, 2),
            "call_charm": round(call_charm_val * call_oi * SHARES_PER_CONTRACT, 2),
            "put_charm": round(put_charm_val * put_oi * SHARES_PER_CONTRACT, 2),
            "net_charm": round((call_charm_val * call_oi + put_charm_val * put_oi) * SHARES_PER_CONTRACT, 2),
            "call_vanna": round(call_vanna_val * call_oi * SHARES_PER_CONTRACT, 2),
            "put_vanna": round(put_vanna_val * put_oi * SHARES_PER_CONTRACT, 2),
            "net_vanna": round((call_vanna_val * call_oi + put_vanna_val * put_oi) * SHARES_PER_CONTRACT, 2),
        })

    # GEX flip point: where net GEX crosses zero (from positive to negative or vice versa)
    flip_point = _find_gex_flip(gex_by_strike, spot)

    # Absolute Gamma Strike: strike with highest absolute net GEX
    abs_gamma_strike = max(gex_by_strike, key=lambda x: abs(x["net_gex"]))["strike"] if gex_by_strike else spot

    # Regime: when flip point exists, use it. Otherwise infer from total GEX sign.
    total_gex = total_call_gex + total_put_gex
    if flip_point is not None:
        regime = "POSITIVE_GAMMA" if spot >= flip_point else "NEGATIVE_GAMMA"
    else:
        regime = "POSITIVE_GAMMA" if total_gex >= 0 else "NEGATIVE_GAMMA"

    # Net charm & vanna totals
    total_charm = sum(s["net_charm"] for s in gex_by_strike)
    total_vanna = sum(s["net_vanna"] for s in gex_by_strike)

    entropy = compute_gex_entropy(gex_by_strike, spot)

    return {
        "gex_by_strike": gex_by_strike,
        "total_gex": round(total_gex, 2),
        "total_call_gex": round(total_call_gex, 2),
        "total_put_gex": round(total_put_gex, 2),
        "flip_point": flip_point,
        "abs_gamma_strike": abs_gamma_strike,
        "regime": regime,
        "total_charm": round(total_charm, 2),
        "total_vanna": round(total_vanna, 2),
        "entropy": entropy,
    }


def calculate_aggregate_gex(
    ticker: str,
    max_dte: int = 45,
    risk_free_rate: float = 0.05,
) -> dict:
    """
    Aggregate GEX across the nearest 4-6 expirations for the true dealer picture.
    Each expiry's gamma is naturally weighted by DTE through the BS formula.
    """
    from options_data import get_expirations, get_options_chain, get_spot_price
    from max_pain import calculate_max_pain, find_oi_walls

    exps = get_expirations(ticker, 0, max_dte)
    if not exps:
        return {"error": "No expirations found", "by_strike": []}

    # Take up to 6 nearest expirations
    selected = exps[:6]
    spot = get_spot_price(ticker)
    if spot <= 0:
        return {"error": "Could not fetch spot price", "by_strike": []}

    # Accumulate per-strike GEX across all expirations
    strike_data = {}
    all_calls = []
    all_puts = []
    expiry_details = []

    for exp in selected:
        try:
            chain = get_options_chain(ticker, exp["date"])
        except Exception:
            continue

        dte = max(exp["dte"], 1)
        gex = calculate_gex_profile(chain["calls"], chain["puts"], spot, dte, risk_free_rate)

        expiry_details.append({
            "expiration": exp["date"],
            "dte": exp["dte"],
            "total_gex": gex["total_gex"],
            "n_strikes": len(gex["gex_by_strike"]),
        })

        for s in gex["gex_by_strike"]:
            k = s["strike"]
            if k not in strike_data:
                strike_data[k] = {
                    "strike": k,
                    "call_oi": 0, "put_oi": 0,
                    "call_gex": 0, "put_gex": 0, "net_gex": 0,
                    "net_charm": 0, "net_vanna": 0,
                }
            strike_data[k]["call_oi"] += s["call_oi"]
            strike_data[k]["put_oi"] += s["put_oi"]
            strike_data[k]["call_gex"] += s["call_gex"]
            strike_data[k]["put_gex"] += s["put_gex"]
            strike_data[k]["net_gex"] += s["net_gex"]
            strike_data[k]["net_charm"] += s.get("net_charm", 0)
            strike_data[k]["net_vanna"] += s.get("net_vanna", 0)

        all_calls.extend(chain["calls"])
        all_puts.extend(chain["puts"])

    by_strike = sorted(strike_data.values(), key=lambda x: x["strike"])
    for s in by_strike:
        for field in ("call_gex", "put_gex", "net_gex", "net_charm", "net_vanna"):
            s[field] = round(s[field], 2)

    flip_point = _find_gex_flip(by_strike, spot)
    abs_gamma_strike = max(by_strike, key=lambda x: abs(x["net_gex"]))["strike"] if by_strike else spot
    if flip_point is not None:
        regime = "POSITIVE_GAMMA" if spot >= flip_point else "NEGATIVE_GAMMA"
    else:
        total_net = sum(s["net_gex"] for s in by_strike)
        regime = "POSITIVE_GAMMA" if total_net >= 0 else "NEGATIVE_GAMMA"

    total_gex = sum(s["net_gex"] for s in by_strike)
    total_call_gex = sum(s["call_gex"] for s in by_strike)
    total_put_gex = sum(s["put_gex"] for s in by_strike)
    total_charm = sum(s["net_charm"] for s in by_strike)
    total_vanna = sum(s["net_vanna"] for s in by_strike)

    # Aggregated max pain and walls from combined OI
    pain = calculate_max_pain(all_calls, all_puts)
    walls = find_oi_walls(all_calls, all_puts, spot)

    return {
        "ticker": ticker,
        "spot": spot,
        "n_expirations": len(expiry_details),
        "expirations_used": expiry_details,
        "regime": regime,
        "flip_point": flip_point,
        "abs_gamma_strike": abs_gamma_strike,
        "total_gex": round(total_gex, 2),
        "total_call_gex": round(total_call_gex, 2),
        "total_put_gex": round(total_put_gex, 2),
        "total_charm": round(total_charm, 2),
        "total_vanna": round(total_vanna, 2),
        "by_strike": by_strike,
        "max_pain": pain["max_pain"],
        "call_wall": walls["call_wall"],
        "put_wall": walls["put_wall"],
    }


def compute_gex_entropy(gex_by_strike: list[dict], spot: float, atm_range_pct: float = 10.0) -> dict:
    """
    Shannon entropy of the GEX distribution — measures how concentrated
    dealer gamma is across strikes. Low entropy = gamma clustered at few
    strikes = system near phase transition = unstable.
    """
    if not gex_by_strike:
        return {"entropy": 1.0, "entropy_norm": 1.0, "regime": "DISPERSED",
                "description": "No data", "n_strikes": 0, "top_concentrations": []}

    relevant = [
        s for s in gex_by_strike
        if abs(s["strike"] - spot) / spot * 100 <= atm_range_pct
        and abs(s["net_gex"]) > 0
    ]

    if len(relevant) < 3:
        return {"entropy": 1.0, "entropy_norm": 1.0, "regime": "DISPERSED",
                "description": "Too few strikes", "n_strikes": len(relevant),
                "top_concentrations": []}

    gex_abs = np.array([abs(s["net_gex"]) for s in relevant])
    total = gex_abs.sum()
    if total <= 0:
        return {"entropy": 1.0, "entropy_norm": 1.0, "regime": "DISPERSED",
                "description": "Zero total GEX", "n_strikes": len(relevant),
                "top_concentrations": []}

    probs = gex_abs / total
    probs = probs[probs > 0]

    H = float(-np.sum(probs * np.log(probs)))
    H_max = np.log(len(probs))
    H_norm = float(H / H_max) if H_max > 0 else 1.0

    # Top 3 concentrations
    sorted_idx = np.argsort(gex_abs)[::-1][:3]
    top = [{"strike": relevant[i]["strike"],
            "gex_share_pct": round(float(gex_abs[i] / total * 100), 1)}
           for i in sorted_idx if i < len(relevant)]

    if H_norm < 0.3:
        regime = "CRITICAL"
        desc = f"Gamma concentrated at ${top[0]['strike']:.0f} ({top[0]['gex_share_pct']:.0f}%) — phase transition risk"
    elif H_norm < 0.5:
        regime = "APPROACHING"
        desc = "Significant gamma clustering — elevated instability"
    elif H_norm < 0.7:
        regime = "MODERATE"
        desc = "Some gamma clustering but overall stable"
    else:
        regime = "DISPERSED"
        desc = "Gamma evenly distributed — stable equilibrium"

    return {
        "entropy": round(H, 4),
        "entropy_norm": round(H_norm, 3),
        "regime": regime,
        "description": desc,
        "n_strikes": len(relevant),
        "top_concentrations": top,
    }


def _find_gex_flip(gex_by_strike: list[dict], spot: float) -> Optional[float]:
    """Find the strike where net GEX crosses zero, nearest to spot."""
    if len(gex_by_strike) < 2:
        return None

    crossings = []
    for i in range(len(gex_by_strike) - 1):
        g1 = gex_by_strike[i]["net_gex"]
        g2 = gex_by_strike[i + 1]["net_gex"]
        if g1 * g2 < 0:
            s1 = gex_by_strike[i]["strike"]
            s2 = gex_by_strike[i + 1]["strike"]
            # Linear interpolation
            flip = s1 + (s2 - s1) * abs(g1) / (abs(g1) + abs(g2))
            crossings.append(round(flip, 2))

    if not crossings:
        return None

    return min(crossings, key=lambda x: abs(x - spot))
