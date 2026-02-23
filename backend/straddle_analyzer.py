"""
Straddle / Strangle Analyzer.
Scores vol-buying setups using regime data, IV vs realized vol,
channel width, and dealer positioning to determine when non-directional
plays have high probability of success.

Key principle: straddles/strangles profit from BIG moves.
The regime data tells us when big moves are likely:
  - TURBULENT Reynolds → walls break → big moves
  - Amplified ACF → moves follow through → momentum
  - Phase transition imminent → regime shift
  - Negative Gamma → dealer amplification
  - IV cheap relative to realized vol → good entry
"""

import numpy as np
from typing import Optional


def analyze_straddles(
    calls: list[dict],
    puts: list[dict],
    spot: float,
    dte: int,
    acf: dict,
    reynolds: dict,
    phase: dict,
    gex_regime: str,
    channel: dict,
    price_history: Optional[list[dict]] = None,
    technicals: Optional[dict] = None,
    key_levels: Optional[dict] = None,
    vrp_data: Optional[dict] = None,
) -> dict:
    re_gamma = reynolds.get("reynolds_number", 0)
    re_regime = reynolds.get("regime", "LAMINAR")
    atm_iv = reynolds.get("atm_iv", 0.3)
    acf1 = acf.get("mean_acf1", 0)
    pct_amp = acf.get("pct_amplified", 0)
    phase_regime = phase.get("regime", "LAMINAR")
    ch_width = channel.get("width_pct", 0) or 0

    atr_data = (technicals or {}).get("atr", {})
    atr_pct = atr_data.get("atr_pct", 0)

    # --- Find ATM options ---
    straddle = _build_straddle(calls, puts, spot)
    strangle = _build_strangle(calls, puts, spot)

    # --- IV vs Realized Vol ---
    iv_rv = _compute_iv_vs_rv(atm_iv, price_history, dte)

    entropy_regime = (technicals or {}).get("_gex_entropy", {}).get("regime", "DISPERSED")

    # --- Score the setup ---
    regime_score = _score_regime(re_gamma, re_regime, acf1, pct_amp, gex_regime)
    iv_score = _score_iv(iv_rv["iv_rv_ratio"], atm_iv)
    catalyst_score = _score_catalyst(phase, acf, ch_width)
    structural_score = _score_structural(gex_regime, channel, re_gamma, straddle, atr_pct, entropy_regime)

    total = regime_score + iv_score + catalyst_score + structural_score

    # --- VRP Adjustment ---
    # Bakshi & Kapadia (2003) documented a structurally negative VRP, but
    # Dew-Becker & Giglio (Chicago Fed WP 2025-17) show index option alphas
    # have converged to zero over the past 15 years as intermediary frictions
    # declined. We retain VRP as a signal but with reduced penalties —
    # extreme premiums still matter, but the "structural headwind" is weaker
    # than the original Dark Matter thesis suggested.
    vrp = vrp_data or {}
    vrp_ctx = vrp.get("context", "N/A")
    vrp_adj = vrp.get("vrp_gex_adjusted", 0)
    vrp_drag = 0
    vrp_headwind_note = ""

    if vrp_ctx == "HIGH_PREMIUM":
        vrp_drag = -6
        vrp_headwind_note = f"Elevated VRP ({vrp_adj:+.1f} var pts) — IV well above GEX-implied realized vol, entry is expensive"
    elif vrp_ctx == "MODERATE_PREMIUM":
        vrp_drag = -3
        vrp_headwind_note = f"Moderate VRP ({vrp_adj:+.1f}) — slightly overpaying but manageable with catalyst"
    elif vrp_ctx == "SMALL_PREMIUM":
        vrp_drag = -1
        vrp_headwind_note = "Small VRP cost — near fair value"
    elif vrp_ctx == "DISCOUNT":
        vrp_drag = 10
        vrp_headwind_note = f"Negative VRP ({vrp_adj:+.1f}) — options are cheap vs GEX-implied vol, favorable entry"

    total += vrp_drag

    # --- Verdict ---
    verdict, verdict_label = _determine_verdict(
        total, iv_rv["iv_rv_ratio"], re_regime, straddle
    )

    # --- Reasoning ---
    reasoning = _build_reasoning(
        re_gamma, re_regime, acf1, pct_amp, gex_regime,
        iv_rv, phase, channel, straddle, atr_pct
    )

    # --- Warnings ---
    warnings = _build_warnings(
        straddle, strangle, dte, atm_iv, iv_rv, re_regime, atr_pct
    )
    if vrp_headwind_note:
        warnings.insert(0, vrp_headwind_note)

    # --- DTE and sizing guidance ---
    if verdict in ("BUY_STRADDLE", "BUY_STRANGLE"):
        if re_regime == "TURBULENT":
            suggested_dte = "5-10 DTE — move expected soon"
        else:
            suggested_dte = "10-21 DTE — give the catalyst time"
        suggested_sizing = "1-2% of account — defined risk (max loss = premium paid)"
    elif verdict == "CONSIDER":
        suggested_dte = "14-21 DTE — extra time for setup to develop"
        suggested_sizing = "0.5-1% of account — smaller size for marginal setups"
    else:
        suggested_dte = "N/A — setup not recommended"
        suggested_sizing = "0% — stay flat"

    # ATR vs breakeven context
    be_pct = straddle.get("required_move_pct", 0)
    if atr_pct > 0 and be_pct > 0:
        atr_coverage = round(atr_pct / be_pct, 2)
        atr_days_to_be = round(be_pct / atr_pct, 1) if atr_pct > 0 else 99
    else:
        atr_coverage = 0
        atr_days_to_be = 0

    # Historical move probability
    move_prob = _compute_move_probability(price_history, dte, be_pct)

    # Theta schedule
    theta_schedule = _compute_theta_schedule(straddle, dte, atm_iv, spot)

    # P/L at key levels (including VWAP)
    vwap_data = (technicals or {}).get("vwap", {})
    pnl_scenarios = _compute_pnl_scenarios(
        straddle, strangle, spot, channel, key_levels, vwap_data,
    )

    return {
        "straddle": straddle,
        "strangle": strangle,
        "iv_vs_rv": iv_rv,
        "atr_context": {
            "atr_pct": round(atr_pct, 2),
            "breakeven_pct": round(be_pct, 2),
            "atr_coverage": atr_coverage,
            "days_to_breakeven": atr_days_to_be,
        },
        "move_probability": move_prob,
        "theta_schedule": theta_schedule,
        "pnl_scenarios": pnl_scenarios,
        "score": {
            "total": total,
            "regime": regime_score,
            "iv": iv_score,
            "catalyst": catalyst_score,
            "structural": structural_score,
            "vrp_drag": vrp_drag,
        },
        "vrp": {
            "drag": vrp_drag,
            "context": vrp_ctx,
            "note": vrp_headwind_note,
            "vrp_gex_adjusted": vrp_adj,
        },
        "verdict": verdict,
        "verdict_label": verdict_label,
        "reasoning": reasoning,
        "warnings": warnings,
        "suggested_dte": suggested_dte,
        "suggested_sizing": suggested_sizing,
    }


def _build_straddle(calls, puts, spot):
    """Find ATM call + put, compute cost and breakevens."""
    atm_call = _find_nearest_option(calls, spot)
    atm_put = _find_nearest_option(puts, spot)

    if not atm_call or not atm_put:
        return _empty_straddle(spot)

    strike = float(atm_call["strike"])
    call_mid = _mid_price(atm_call)
    put_mid = _mid_price(atm_put)
    total = call_mid + put_mid

    if total <= 0:
        return _empty_straddle(spot)

    return {
        "strike": strike,
        "call_premium": round(call_mid, 2),
        "put_premium": round(put_mid, 2),
        "total_cost": round(total, 2),
        "total_cost_per_contract": round(total * 100, 2),
        "upper_breakeven": round(strike + total, 2),
        "lower_breakeven": round(strike - total, 2),
        "required_move_pct": round(total / spot * 100, 2),
        "max_loss": round(total, 2),
        "call_iv": round(float(atm_call.get("impliedVolatility", 0)) * 100, 1),
        "put_iv": round(float(atm_put.get("impliedVolatility", 0)) * 100, 1),
    }


def _build_strangle(calls, puts, spot):
    """Find ~3-5% OTM call + put for a strangle."""
    otm_call_target = spot * 1.03
    otm_put_target = spot * 0.97

    otm_call = _find_nearest_option(calls, otm_call_target, direction="above")
    otm_put = _find_nearest_option(puts, otm_put_target, direction="below")

    if not otm_call or not otm_put:
        return _empty_strangle(spot)

    call_strike = float(otm_call["strike"])
    put_strike = float(otm_put["strike"])
    call_mid = _mid_price(otm_call)
    put_mid = _mid_price(otm_put)
    total = call_mid + put_mid

    if total <= 0:
        return _empty_strangle(spot)

    return {
        "call_strike": call_strike,
        "put_strike": put_strike,
        "call_premium": round(call_mid, 2),
        "put_premium": round(put_mid, 2),
        "total_cost": round(total, 2),
        "total_cost_per_contract": round(total * 100, 2),
        "upper_breakeven": round(call_strike + total, 2),
        "lower_breakeven": round(put_strike - total, 2),
        "required_move_pct": round(
            max(
                abs(call_strike + total - spot) / spot,
                abs(spot - put_strike + total) / spot,
            ) * 100, 2
        ),
        "max_loss": round(total, 2),
        "width": round(call_strike - put_strike, 2),
        "width_pct": round((call_strike - put_strike) / spot * 100, 2),
    }


def _compute_iv_vs_rv(atm_iv, price_history, dte):
    """Compare implied vol to realized vol from recent price action."""
    rv = 0
    rv_context = "N/A"

    if price_history and len(price_history) >= 10:
        closes = [bar["close"] for bar in price_history if bar.get("close", 0) > 0]
        if len(closes) >= 10:
            log_returns = np.diff(np.log(closes))
            rv = float(np.std(log_returns) * np.sqrt(252))

    if atm_iv <= 0:
        atm_iv = 0.3

    ratio = atm_iv / rv if rv > 0.01 else 1.0

    if ratio < 0.85:
        iv_context = "CHEAP"
    elif ratio < 1.15:
        iv_context = "FAIR"
    else:
        iv_context = "EXPENSIVE"

    return {
        "atm_iv": round(atm_iv * 100, 1),
        "realized_vol": round(rv * 100, 1),
        "iv_rv_ratio": round(ratio, 2),
        "iv_context": iv_context,
    }


def _score_regime(re_gamma, re_regime, acf1, pct_amp, gex_regime):
    """0-25: Does the regime support big moves?"""
    score = 0

    if re_regime == "TURBULENT":
        score += 12
    elif re_regime == "TRANSITIONAL":
        score += 6

    if acf1 > 0.10:
        score += 8
    elif acf1 > 0.05:
        score += 5
    elif acf1 > 0:
        score += 2
    elif acf1 < -0.10:
        score -= 3

    if pct_amp > 15:
        score += 5
    elif pct_amp > 10:
        score += 3

    return max(0, min(25, score))


def _score_iv(iv_rv_ratio, atm_iv):
    """0-25: Is IV cheap enough to make buying vol worthwhile?"""
    score = 0

    if iv_rv_ratio < 0.75:
        score += 20
    elif iv_rv_ratio < 0.85:
        score += 15
    elif iv_rv_ratio < 1.0:
        score += 10
    elif iv_rv_ratio < 1.15:
        score += 5
    elif iv_rv_ratio < 1.3:
        score += 2

    if atm_iv < 0.20:
        score += 5
    elif atm_iv < 0.30:
        score += 3

    return max(0, min(25, score))


def _score_catalyst(phase, acf, ch_width):
    """0-25: Are there catalysts for a big move?"""
    score = 0

    phase_regime = phase.get("regime", "LAMINAR")
    if phase_regime == "TURBULENT":
        score += 10
    elif phase_regime == "APPROACHING":
        score += 6

    dist = phase.get("distance_to_transition", 0)
    if 0 < dist < 5:
        score += 5

    if acf.get("at_squeeze_ceiling", False):
        score += 5

    stability = acf.get("stability", "STABLE")
    if stability == "UNSTABLE":
        score += 5
    elif stability == "SHIFTING":
        score += 3

    if ch_width > 0 and ch_width < 2:
        score += 5
    elif ch_width > 0 and ch_width < 4:
        score += 2

    # Self-excitation boost
    sei = acf.get("self_excitation", {}).get("sei", 0)
    if sei > 150:
        score += 6
    elif sei > 80:
        score += 3

    return max(0, min(25, score))


def _score_structural(gex_regime, channel, re_gamma, straddle, atr_pct=0, entropy_regime="DISPERSED"):
    """0-25: Does the GEX structure support a vol expansion?"""
    score = 0

    if gex_regime == "NEGATIVE_GAMMA":
        score += 8
    else:
        score += 2

    ch_width = channel.get("width_pct", 0) or 0
    if ch_width > 0 and ch_width < 3:
        score += 5
    elif ch_width > 0 and ch_width < 5:
        score += 3

    if re_gamma > 1.5:
        score += 4
    elif re_gamma > 1.0:
        score += 2

    # ATR vs breakeven: the key question — can the stock actually move enough?
    be_pct = straddle.get("required_move_pct", 99)
    if atr_pct > 0 and be_pct > 0:
        atr_coverage = atr_pct / be_pct
        if atr_coverage > 1.5:
            score += 8
        elif atr_coverage > 1.0:
            score += 5
        elif atr_coverage > 0.7:
            score += 2
    elif be_pct < 1.5:
        score += 3
    elif be_pct < 2.5:
        score += 1

    # GEX entropy: concentrated gamma = approaching phase transition
    if entropy_regime == "CRITICAL":
        score += 8
    elif entropy_regime == "APPROACHING":
        score += 4

    return max(0, min(25, score))


def _determine_verdict(total, iv_rv_ratio, re_regime, straddle):
    be_pct = straddle.get("required_move_pct", 99)

    if total >= 70 and iv_rv_ratio < 1.2:
        return "BUY_STRADDLE", "Strong Setup — Regime + IV Favor Big Move"
    elif total >= 60 and iv_rv_ratio < 1.3:
        if be_pct > 3.5:
            return "BUY_STRANGLE", "Good Setup — Strangle Preferred (Lower Cost)"
        return "BUY_STRADDLE", "Good Setup — Conditions Support Vol Expansion"
    elif total >= 45:
        return "CONSIDER", "Marginal — Some Factors Align, Watch for Confirmation"
    else:
        return "AVOID", "Conditions Favor Range-Bound / Mean-Reversion — Don't Buy Vol"


def _build_reasoning(
    re_gamma, re_regime, acf1, pct_amp, gex_regime,
    iv_rv, phase, channel, straddle, atr_pct=0
):
    reasons = []

    if re_regime == "TURBULENT":
        reasons.append(
            f"Reynolds TURBULENT (Re={re_gamma:.2f}) — dealers overwhelmed, "
            f"walls become accelerators, big moves follow"
        )
    elif re_regime == "TRANSITIONAL":
        reasons.append(
            f"Reynolds TRANSITIONAL (Re={re_gamma:.2f}) — approaching breakout "
            f"territory, walls may not hold"
        )
    else:
        reasons.append(
            f"Reynolds LAMINAR (Re={re_gamma:.2f}) — dealers absorb flow, "
            f"moves get dampened, range-bound"
        )

    if acf1 > 0.05:
        reasons.append(
            f"ACF amplified ({acf1:+.3f}, {pct_amp:.0f}% amp days) — "
            f"moves follow through, momentum regime"
        )
    elif acf1 < -0.05:
        reasons.append(
            f"ACF dampened ({acf1:+.3f}) — moves reverse, "
            f"mean-reversion regime hurts straddles"
        )
    else:
        reasons.append(f"ACF neutral ({acf1:+.3f}) — no strong directional persistence")

    ctx = iv_rv["iv_context"]
    ratio = iv_rv["iv_rv_ratio"]
    if ctx == "CHEAP":
        reasons.append(
            f"IV is CHEAP vs realized (ratio={ratio:.2f}) — "
            f"market underpricing actual volatility, good entry"
        )
    elif ctx == "FAIR":
        reasons.append(
            f"IV is fairly priced (ratio={ratio:.2f}) — "
            f"need a regime catalyst to justify buying"
        )
    else:
        reasons.append(
            f"IV is EXPENSIVE (ratio={ratio:.2f}) — "
            f"premium is rich, breakevens are far, tough entry"
        )

    if gex_regime == "NEGATIVE_GAMMA":
        reasons.append(
            "Negative Gamma regime — dealer hedging amplifies moves in both directions"
        )
    else:
        reasons.append(
            "Positive Gamma regime — dealer hedging absorbs moves (headwind for straddles)"
        )

    phase_regime = phase.get("regime", "LAMINAR")
    if phase_regime == "TURBULENT":
        reasons.append("Phase transition active — regime instability adds move potential")
    elif phase.get("warning"):
        reasons.append(f"Phase alert: {phase['warning']}")

    be_pct = straddle.get("required_move_pct", 0)
    if atr_pct > 0 and be_pct > 0:
        coverage = atr_pct / be_pct
        days_to_be = be_pct / atr_pct
        if coverage > 1.5:
            reasons.append(
                f"ATR ({atr_pct:.1f}%) is {coverage:.1f}x the breakeven ({be_pct:.1f}%) — "
                f"stock routinely moves enough to profit in ~{days_to_be:.0f} day"
            )
        elif coverage > 1.0:
            reasons.append(
                f"ATR ({atr_pct:.1f}%) covers the breakeven ({be_pct:.1f}%) — "
                f"achievable in ~{days_to_be:.0f} day of normal range"
            )
        else:
            reasons.append(
                f"ATR ({atr_pct:.1f}%) is only {coverage:.1f}x the breakeven ({be_pct:.1f}%) — "
                f"needs ~{days_to_be:.0f} days of trending to reach breakeven"
            )

    return reasons


def _build_warnings(straddle, strangle, dte, atm_iv, iv_rv, re_regime, atr_pct=0):
    warnings = []

    be_pct = straddle.get("required_move_pct", 0)
    if be_pct > 3:
        warnings.append(
            f"Straddle requires {be_pct:.1f}% move to breakeven — "
            f"that's a significant move, consider a cheaper strangle instead"
        )

    if dte <= 5:
        daily_theta_pct = straddle.get("total_cost", 0) / max(dte, 1)
        warnings.append(
            f"Only {dte} DTE — theta decay ~${daily_theta_pct:.2f}/day per share. "
            f"Need the move quickly"
        )
    elif dte <= 10:
        warnings.append(
            f"{dte} DTE — moderate theta pressure. "
            f"Close within 5 days if move doesn't develop"
        )

    if iv_rv["iv_context"] == "EXPENSIVE":
        warnings.append(
            "IV is expensive — you're paying above-realized premium. "
            "A vol crush (IV dropping) will hurt even if the stock moves"
        )

    if re_regime == "LAMINAR":
        warnings.append(
            "LAMINAR regime — dealers are absorbing moves. "
            "Straddles typically lose money in range-bound conditions"
        )

    strangle_cost = strangle.get("total_cost", 0)
    straddle_cost = straddle.get("total_cost", 0)
    if straddle_cost > 0 and strangle_cost > 0:
        savings_pct = (1 - strangle_cost / straddle_cost) * 100
        if savings_pct > 30:
            warnings.append(
                f"Strangle is {savings_pct:.0f}% cheaper than straddle — "
                f"consider the strangle if you're confident in a large move"
            )

    be_pct = straddle.get("required_move_pct", 0)
    if atr_pct > 0 and be_pct > 0:
        coverage = atr_pct / be_pct
        if coverage < 0.7:
            warnings.append(
                f"ATR ({atr_pct:.1f}%) is well below breakeven ({be_pct:.1f}%) — "
                f"stock doesn't typically move enough daily to profit"
            )

    return warnings


# ---- Move Probability ----

def _compute_move_probability(price_history, dte, breakeven_pct):
    """
    Backtest: over the last year of data, what % of N-day rolling windows
    had a max move (high-low range) >= the breakeven %?
    This tells you the empirical probability of the straddle reaching breakeven.
    """
    if not price_history or len(price_history) < 30 or dte <= 0 or breakeven_pct <= 0:
        return {"probability": 0, "sample_size": 0, "windows": []}

    closes = np.array([bar["close"] for bar in price_history if bar.get("close", 0) > 0])
    highs = np.array([bar["high"] for bar in price_history if bar.get("high", 0) > 0])
    lows = np.array([bar["low"] for bar in price_history if bar.get("low", 0) > 0])

    min_len = min(len(closes), len(highs), len(lows))
    closes = closes[:min_len]
    highs = highs[:min_len]
    lows = lows[:min_len]

    if min_len < dte + 5:
        return {"probability": 0, "sample_size": 0, "windows": []}

    # Test multiple DTE windows to help with expiration selection
    results = []
    for test_dte in [dte, 5, 7, 10, 14, 21]:
        if test_dte > min_len - 5:
            continue
        hits = 0
        total = 0
        for i in range(min_len - test_dte):
            window_high = float(np.max(highs[i:i + test_dte]))
            window_low = float(np.min(lows[i:i + test_dte]))
            entry_price = float(closes[i])
            if entry_price <= 0:
                continue
            max_move_up = (window_high - entry_price) / entry_price * 100
            max_move_down = (entry_price - window_low) / entry_price * 100
            max_move = max(max_move_up, max_move_down)
            if max_move >= breakeven_pct:
                hits += 1
            total += 1

        pct = round(hits / total * 100, 1) if total > 0 else 0
        results.append({
            "dte": test_dte,
            "probability": pct,
            "sample_size": total,
            "is_current": test_dte == dte,
        })

    # Deduplicate and sort
    seen = set()
    unique = []
    for r in results:
        if r["dte"] not in seen:
            seen.add(r["dte"])
            unique.append(r)
    unique.sort(key=lambda r: r["dte"])

    current = next((r for r in unique if r["is_current"]), unique[0] if unique else {"probability": 0})

    return {
        "probability": current["probability"],
        "sample_size": current.get("sample_size", 0),
        "windows": unique,
    }


# ---- Theta Schedule ----

def _compute_theta_schedule(straddle, dte, atm_iv, spot):
    """
    Estimate theta decay over the life of the straddle.
    Uses the approximation: theta ≈ -(S * σ) / (2 * √(2π * T))
    for an ATM straddle, which gives the daily dollar cost of holding.
    """
    total_cost = straddle.get("total_cost", 0)
    if total_cost <= 0 or dte <= 0 or atm_iv <= 0:
        return {"daily_theta": 0, "schedule": [], "theta_pct_of_premium": 0}

    schedule = []
    remaining_value = total_cost

    for day in range(1, min(dte + 1, 22)):
        t_remaining = max(dte - day, 0.5) / 365
        # ATM straddle theta: -(S * σ) / (2 * sqrt(2π * T))
        daily_theta = (spot * atm_iv) / (2 * np.sqrt(2 * np.pi * t_remaining * 365))
        daily_theta_per_share = daily_theta / 365
        remaining_value = max(remaining_value - daily_theta_per_share, 0)
        pct_lost = round((1 - remaining_value / total_cost) * 100, 1) if total_cost > 0 else 0

        schedule.append({
            "day": day,
            "days_left": dte - day,
            "theta": round(daily_theta_per_share, 3),
            "cumulative_decay_pct": pct_lost,
            "remaining_value": round(remaining_value, 2),
        })

    day1_theta = schedule[0]["theta"] if schedule else 0

    return {
        "daily_theta": round(day1_theta, 3),
        "daily_theta_pct": round(day1_theta / total_cost * 100, 1) if total_cost > 0 else 0,
        "schedule": schedule,
        "half_life_day": next((s["day"] for s in schedule if s["cumulative_decay_pct"] >= 50), dte),
    }


# ---- P/L Scenarios ----

def _compute_pnl_scenarios(straddle, strangle, spot, channel, key_levels, vwap_data=None):
    """
    Compute straddle P/L at key price levels (dealer walls, channel bounds,
    VWAP levels). Shows exactly how much you make/lose if price reaches each level.
    """
    kl = key_levels or {}
    vw = vwap_data or {}
    total_cost = straddle.get("total_cost", 0)
    strike = straddle.get("strike", spot)

    if total_cost <= 0:
        return []

    scenarios = []

    # Key dealer levels
    level_entries = [
        ("Call Wall", kl.get("call_wall", {}).get("strike") if isinstance(kl.get("call_wall"), dict) else kl.get("call_wall")),
        ("Put Wall", kl.get("put_wall", {}).get("strike") if isinstance(kl.get("put_wall"), dict) else kl.get("put_wall")),
        ("Max Pain", kl.get("max_pain")),
        ("Ch Floor", channel.get("floor")),
        ("Ch Ceiling", channel.get("ceiling")),
    ]

    # VWAP levels
    vwap_20 = vw.get("vwap_20d")
    if vwap_20 and isinstance(vwap_20, dict):
        level_entries.append(("VWAP 20d", vwap_20.get("value")))
        if vwap_20.get("upper_1"):
            level_entries.append(("VWAP +1σ", vwap_20["upper_1"]))
        if vwap_20.get("lower_1"):
            level_entries.append(("VWAP -1σ", vwap_20["lower_1"]))

    for label, price in level_entries:
        if not price or price <= 0:
            continue
        intrinsic = abs(price - strike)
        pnl = intrinsic - total_cost
        pnl_pct = round(pnl / total_cost * 100, 1)
        move_pct = round(abs(price - spot) / spot * 100, 2)
        scenarios.append({
            "label": label,
            "price": round(price, 2),
            "move_pct": move_pct,
            "pnl": round(pnl, 2),
            "pnl_pct": pnl_pct,
            "profitable": pnl > 0,
        })

    # Deduplicate scenarios at the same price
    seen = {}
    unique = []
    for s in scenarios:
        key = round(s["price"], 1)
        if key in seen:
            seen[key]["label"] += " + " + s["label"]
        else:
            seen[key] = s
            unique.append(s)

    unique.sort(key=lambda s: s["price"])
    return unique


# ---- Helpers ----

def _find_nearest_option(options, target_strike, direction=None):
    """Find the option closest to the target strike."""
    if not options:
        return None

    if direction == "above":
        candidates = [o for o in options if float(o["strike"]) >= target_strike]
    elif direction == "below":
        candidates = [o for o in options if float(o["strike"]) <= target_strike]
    else:
        candidates = options

    if not candidates:
        candidates = options

    return min(candidates, key=lambda o: abs(float(o["strike"]) - target_strike))


def _mid_price(option):
    """Compute mid price from bid/ask, fallback to lastPrice."""
    bid = float(option.get("bid", 0))
    ask = float(option.get("ask", 0))
    if bid > 0 and ask > 0:
        return (bid + ask) / 2
    return float(option.get("lastPrice", 0))


def _empty_straddle(spot):
    return {
        "strike": round(spot),
        "call_premium": 0,
        "put_premium": 0,
        "total_cost": 0,
        "total_cost_per_contract": 0,
        "upper_breakeven": spot,
        "lower_breakeven": spot,
        "required_move_pct": 0,
        "max_loss": 0,
        "call_iv": 0,
        "put_iv": 0,
    }


def _empty_strangle(spot):
    return {
        "call_strike": round(spot * 1.03),
        "put_strike": round(spot * 0.97),
        "call_premium": 0,
        "put_premium": 0,
        "total_cost": 0,
        "total_cost_per_contract": 0,
        "upper_breakeven": spot * 1.03,
        "lower_breakeven": spot * 0.97,
        "required_move_pct": 3.0,
        "max_loss": 0,
        "width": round(spot * 0.06),
        "width_pct": 6.0,
    }
