"""
Directional Engine — BUY-ONLY thesis classification.
Translates ACF regime, Reynolds, GEX channel, and dealer levels
into actionable directional guidance for option buyers.

Ported and adapted from OscillationPriming positions.py.
"""

import numpy as np


def classify_thesis(
    spot: float,
    acf: dict,
    reynolds: dict,
    phase: dict,
    gex_regime: str,
    channel: dict,
    channel_strat: dict,
    max_pain: float,
    call_wall: dict,
    put_wall: dict,
    flip_point: float,
    abs_gamma_strike: float,
    total_charm: float,
    total_vanna: float,
    dte: int,
    technicals: dict = None,
    total_gex: float = 0,
) -> dict:
    """
    The core directional classification.
    Returns thesis, bias, positions, level actions, and guidance.
    """
    acf_regime = acf.get("regime", "NEUTRAL")
    acf1 = acf.get("mean_acf1", 0)
    pct_amp = acf.get("pct_amplified", 0)
    pct_damp = acf.get("pct_dampened", 0)
    stability = acf.get("stability", "STABLE")

    re_gamma = reynolds.get("reynolds_number", 0)
    re_regime = reynolds.get("regime", "LAMINAR")
    cp_ratio = reynolds.get("call_put_ratio", 1)
    atm_iv = reynolds.get("atm_iv", 0)

    phase_regime = phase.get("regime", "LAMINAR")

    ch_floor = channel.get("floor")
    ch_ceiling = channel.get("ceiling")
    ch_pos = channel.get("channel_position")
    ch_width = channel.get("width_pct")
    ch_strategy = channel_strat.get("strategy", "WAIT")
    ch_edge = channel_strat.get("edge_type", "NEUTRAL")

    # ---- Technicals context (needed for thesis + bias + wall break) ----
    tech = technicals or {}
    tech_trend = tech.get("trend", {})
    tech_bias_dir = tech_trend.get("tech_bias", "NEUTRAL")
    tech_score = tech_trend.get("trend_score", 0)
    tech_ma = tech.get("moving_averages", {})
    tech_rs = tech.get("relative_strength", {})
    tech_atr = tech.get("atr", {})
    ma_alignment = tech_ma.get("alignment", 0)

    # Beta-adjusted Reynolds: low-beta stocks are more vulnerable to gamma squeezes
    beta = tech_rs.get("beta_60d", 1.0)
    beta_adj = 1.0 / max(beta, 0.3)
    re_gamma_adj = re_gamma * beta_adj

    # Self-excitation from ACF module (Hawkes-inspired)
    sei_data = acf.get("self_excitation", {})
    sei = sei_data.get("sei", 0)
    sei_regime = sei_data.get("regime", "NONE")

    # GEX entropy (passed via technicals or direct)
    gex_entropy = tech.get("_gex_entropy", {})
    entropy_regime = gex_entropy.get("regime", "DISPERSED")

    atr_pct_pre = tech_atr.get("atr_pct", 0)
    atr_dollar_pre = tech_atr.get("atr", 0)

    wall_break = _estimate_wall_break_probability(
        re_gamma, re_regime, acf1, pct_amp, pct_damp, phase_regime, cp_ratio,
        beta_adj=beta_adj,
        atr_dollar=atr_dollar_pre, dte=dte,
        call_wall_strike=call_wall.get("strike", 0) if isinstance(call_wall, dict) else 0,
        put_wall_strike=put_wall.get("strike", 0) if isinstance(put_wall, dict) else 0,
        spot=spot,
        sei=sei,
        entropy_regime=entropy_regime,
        gex_regime=gex_regime,
    )

    # ---- Thesis decision tree ----
    # Reynolds is structural (cause), ACF is observational (effect).
    # When they conflict, Reynolds wins.
    # Beta-adjusted Re used for thresholds — low-beta names trip earlier.
    # Self-excitation provides ACF confirmation.

    acf_net_momentum = (pct_amp > pct_damp and (pct_amp > 13 or acf1 > 0.05)) or \
                       (sei_regime in ("HIGH_EXCITATION", "MODERATE_EXCITATION") and acf1 > 0)

    # If entropy is CRITICAL and Re is transitional, treat as turbulent
    effective_re = re_gamma_adj
    if entropy_regime == "CRITICAL" and re_regime == "TRANSITIONAL":
        effective_re = max(effective_re, 1.05)

    if effective_re > 1.0 and acf_net_momentum:
        thesis = "MOMENTUM_BREAKOUT"
    elif effective_re > 1.0:
        thesis = "MOMENTUM_EARLY"
    elif pct_amp > pct_damp and pct_amp > 13 and effective_re < 0.7:
        thesis = "CONFLICTED_PIN"
    elif acf_regime == "SHORT_GAMMA" or acf1 > 0.05:
        thesis = "MOMENTUM_TREND"
    elif acf_regime == "LONG_GAMMA" and acf1 < -0.10:
        thesis = "FADE_MOVES"
    elif acf_regime == "LONG_GAMMA":
        thesis = "FADE_MILD"
    else:
        thesis = "NEUTRAL"

    # ---- Build directional bias ----
    # Pass technicals so bias can incorporate MA alignment + RS for direction
    bias = _compute_bias(
        thesis, spot, flip_point, max_pain, ch_pos,
        total_charm, total_vanna, dte,
        tech_bias_dir, ma_alignment, tech_score,
    )

    # ---- Technicals overlay (upgrades/downgrades conviction) ----
    tech_confirms = False
    tech_conflicts = False
    if bias["direction"] == "BULLISH" and tech_bias_dir in ("BULLISH", "LEAN_BULLISH"):
        tech_confirms = True
    elif bias["direction"] == "BEARISH" and tech_bias_dir in ("BEARISH", "LEAN_BEARISH"):
        tech_confirms = True
    elif bias["direction"] == "BULLISH" and tech_bias_dir in ("BEARISH", "LEAN_BEARISH"):
        tech_conflicts = True
    elif bias["direction"] == "BEARISH" and tech_bias_dir in ("BULLISH", "LEAN_BULLISH"):
        tech_conflicts = True

    if tech_confirms and bias["strength"] == "MODERATE":
        bias["strength"] = "STRONG"
    elif tech_conflicts and bias["strength"] == "STRONG":
        bias["strength"] = "MODERATE"
    elif tech_conflicts and bias["strength"] == "MODERATE":
        bias["strength"] = "WEAK"

    atr_pct = tech_atr.get("atr_pct", 0)
    atr_dollar = tech_atr.get("atr", 0)

    # VWAP context
    tech_vwap = tech.get("vwap", {})
    vwap_20d = tech_vwap.get("vwap_20d", {})
    vwap_level = vwap_20d.get("value") if isinstance(vwap_20d, dict) else None

    # ---- Vol context for strike/sizing adjustment ----
    vol_context = tech.get("_vol_context", {})

    # ---- Build positions ----
    positions = _build_positions(
        thesis, spot, bias, re_gamma, acf1, pct_damp,
        call_wall, put_wall, ch_floor, ch_ceiling, ch_pos,
        ch_strategy, ch_edge, wall_break, atm_iv, dte,
        max_pain, abs_gamma_strike, cp_ratio, total_charm, total_vanna,
        atr_dollar, atr_pct, vol_context,
    )

    # ---- Level actions ----
    level_actions = _build_level_actions(
        spot, thesis, re_regime, gex_regime,
        max_pain, call_wall, put_wall, flip_point,
        abs_gamma_strike, ch_floor, ch_ceiling, wall_break,
        vwap_level=vwap_level,
        atr_dollar=atr_dollar_pre, dte=dte,
    )

    # ---- What to avoid ----
    avoid = _build_avoid_list(thesis, phase_regime)

    # IV context
    if atm_iv > 0.60:
        iv_context = "HIGH"
    elif atm_iv > 0.35:
        iv_context = "MODERATE"
    elif atm_iv > 0:
        iv_context = "LOW"
    else:
        iv_context = "N/A"

    # Technicals summary for the frontend
    tech_context = {
        "confirms_thesis": tech_confirms,
        "conflicts_thesis": tech_conflicts,
        "trend_label": tech_trend.get("trend_label", "UNKNOWN"),
        "trend_desc": tech_trend.get("trend_desc", ""),
        "tech_bias": tech_bias_dir,
        "ma_alignment": tech_ma.get("alignment_label", "UNKNOWN"),
        "rs_label": tech_rs.get("rs_label", "UNKNOWN"),
        "rs_desc": tech_rs.get("rs_desc", ""),
        "atr_pct": tech_atr.get("atr_pct", 0),
        "vwap": tech_vwap.get("context", "N/A"),
        "vwap_desc": tech_vwap.get("context_desc", ""),
        "vwap_level": vwap_level,
        "beta": round(beta, 2),
        "beta_adj_factor": round(beta_adj, 2),
        "re_beta_adj": round(re_gamma_adj, 2),
        "entropy_regime": entropy_regime,
        "sei": round(sei, 3),
        "sei_regime": sei_regime,
    }

    return {
        "thesis": thesis,
        "thesis_label": _thesis_label(thesis),
        "bias": bias,
        "positions": positions,
        "level_actions": level_actions,
        "wall_break": wall_break,
        "avoid": avoid,
        "iv_context": iv_context,
        "atm_iv": round(atm_iv * 100, 1) if atm_iv else 0,
        "tech_context": tech_context,
    }


def _compute_bias(
    thesis, spot, flip_point, max_pain, ch_pos,
    charm, vanna, dte,
    tech_bias_dir="NEUTRAL", ma_alignment=0, tech_score=0,
):
    """
    Determine the primary directional bias.

    Direction is a composite of GEX positioning (flip point) and structural
    technicals (MA alignment, RS). When they strongly disagree, technicals
    get priority for direction — GEX tells you the regime, technicals tell
    you which way the stock is actually going.
    """
    def _resolve_direction(gex_dir):
        """
        Resolve direction conflict between GEX flip-point and technicals.
        Strong technical signals (FULL_BULL/BEAR, ±2 score) override GEX direction.
        Moderate ones leave GEX direction alone (it may be right short-term).
        """
        if abs(tech_score) >= 2:
            # Strong technical signal overrides GEX direction
            tech_dir = "BULLISH" if tech_score >= 2 else "BEARISH"
            if tech_dir != gex_dir:
                return tech_dir
        elif abs(ma_alignment) >= 3:
            # FULL_BULL or FULL_BEAR with moderate RS
            tech_dir = "BULLISH" if ma_alignment >= 3 else "BEARISH"
            if tech_dir != gex_dir:
                return tech_dir
        return gex_dir

    if thesis in ("MOMENTUM_BREAKOUT", "MOMENTUM_EARLY", "MOMENTUM_TREND"):
        gex_dir = "BULLISH" if spot > flip_point else "BEARISH"
        direction = _resolve_direction(gex_dir)
        return {
            "direction": direction,
            "action": "BUY CALLS" if direction == "BULLISH" else "BUY PUTS",
            "style": "RIDE MOMENTUM",
            "description": "Moves follow through. Don't fade — ride the trend.",
            "strength": "STRONG" if thesis == "MOMENTUM_BREAKOUT" else "MODERATE",
        }
    elif thesis in ("FADE_MOVES", "FADE_MILD"):
        # FADE direction: technicals decide the structural lean,
        # max pain only applies when technicals are neutral.
        if abs(tech_score) >= 2 or abs(ma_alignment) >= 3:
            direction = "BULLISH" if tech_score >= 1 or ma_alignment >= 2 else "BEARISH"
            desc = "Dealers dampen moves. Fade against the trend intraday but structural lean follows MAs."
        else:
            direction = "BULLISH" if spot < max_pain else "BEARISH"
            desc = "Dealers dampen moves. Buy the opposite of today's move. Quick in-and-out."
        return {
            "direction": direction,
            "action": "BUY CALLS on dips, BUY PUTS on rips",
            "style": "FADE & MEAN-REVERT",
            "description": desc,
            "strength": "STRONG" if thesis == "FADE_MOVES" else "MODERATE",
        }
    elif thesis == "CONFLICTED_PIN":
        return {
            "direction": "NEUTRAL",
            "action": "BUY toward walls, SELL at walls",
            "style": "PIN & FADE AT WALLS",
            "description": "Walls will hold (Re < 1). Trade TO walls, not through them. Fade the touch.",
            "strength": "MODERATE",
        }
    else:
        if ch_pos is not None and ch_pos < 0.25:
            return {
                "direction": "BULLISH",
                "action": "BUY CALLS near floor",
                "style": "CHANNEL BOUNCE",
                "description": "No strong regime signal but price near GEX floor. Lean bullish.",
                "strength": "WEAK",
            }
        elif ch_pos is not None and ch_pos > 0.75:
            return {
                "direction": "BEARISH",
                "action": "BUY PUTS near ceiling",
                "style": "CHANNEL FADE",
                "description": "No strong regime signal but price near GEX ceiling. Lean bearish.",
                "strength": "WEAK",
            }

        if dte <= 5 and charm < -10000:
            toward = "BEARISH" if spot > max_pain else "BULLISH"
            return {
                "direction": toward,
                "action": f"BUY {'PUTS' if spot > max_pain else 'CALLS'} — charm drift",
                "style": "DRIFT TOWARD MAX PAIN",
                "description": f"Charm flows pulling price toward ${max_pain:.0f}. Short-duration play.",
                "strength": "MODERATE",
            }

        return {
            "direction": "NEUTRAL",
            "action": "WAIT or small lottery",
            "style": "NO CLEAR EDGE",
            "description": "No confirmed regime. Stay flat or take tiny positions.",
            "strength": "NONE",
        }


def _round_strike(price: float, spot: float) -> float:
    if spot > 500:
        inc = 5.0
    elif spot > 100:
        inc = 5.0
    elif spot > 50:
        inc = 2.5
    elif spot > 20:
        inc = 1.0
    else:
        inc = 0.5
    return round(price / inc) * inc


def _atr_max_move(atr_dollar, dte, multiplier=1.0):
    """Estimate the max realistic move over DTE using ATR.
    sqrt(DTE) scaling reflects the random-walk nature of price."""
    if atr_dollar <= 0 or dte <= 0:
        return 0
    return atr_dollar * np.sqrt(dte) * multiplier


def _clamp_strike(desired, spot, atr_dollar, dte, is_call):
    """Ensure a strike is within a reachable range given ATR and DTE."""
    if atr_dollar <= 0 or dte <= 0:
        return desired
    max_move = _atr_max_move(atr_dollar, dte, multiplier=1.5)
    if is_call:
        cap = spot + max_move
        return min(desired, _round_strike(cap, spot))
    else:
        floor = spot - max_move
        return max(desired, _round_strike(floor, spot))


def _clamp_target(target_price, spot, atr_dollar, dte):
    """Ensure a target price is within realistic ATR*sqrt(DTE) range."""
    if atr_dollar <= 0 or dte <= 0:
        return target_price
    max_move = _atr_max_move(atr_dollar, dte, multiplier=2.0)
    upper = spot + max_move
    lower = spot - max_move
    return max(lower, min(upper, target_price))


def _kelly_size(edge_pct, win_prob, iv_hv_ratio, vrp_context="FAIR"):
    """
    Simplified Kelly criterion adapted for option buying (Wysocki 2025).
    
    Kelly fraction = (b*p - q) / b
    where b = odds (payoff ratio), p = win probability, q = 1-p.
    
    We cap at half-Kelly for safety and scale by vol regime:
    expensive IV → smaller, cheap IV → allow more.
    """
    if edge_pct <= 0 or win_prob <= 0:
        return 0, "0%"

    b = max(edge_pct / 100, 0.5)  # payoff ratio (e.g., 2x = 200% target)
    p = min(win_prob / 100, 0.9)
    q = 1 - p

    kelly_full = (b * p - q) / b if b > 0 else 0
    kelly_half = max(0, kelly_full * 0.5)

    # Vol-regime scaling: IV/HV ratio is the primary signal for sizing.
    # VRP context provides a secondary nudge but with reduced weight per
    # Dew-Becker & Giglio (2025) showing structural VRP has largely vanished.
    if iv_hv_ratio > 1.5 or vrp_context == "HIGH_PREMIUM":
        regime_scale = 0.7
    elif iv_hv_ratio > 1.3 and vrp_context == "MODERATE_PREMIUM":
        regime_scale = 0.8
    elif iv_hv_ratio < 0.9 or vrp_context == "DISCOUNT":
        regime_scale = 1.2
    else:
        regime_scale = 1.0

    final_pct = round(kelly_half * regime_scale * 100, 1)
    final_pct = max(0.25, min(5.0, final_pct))  # floor 0.25%, cap 5%

    if final_pct >= 3.0:
        label = f"{final_pct}% (full conviction)"
    elif final_pct >= 1.5:
        label = f"{final_pct}% (standard)"
    elif final_pct >= 0.75:
        label = f"{final_pct}% (reduced — vol headwind)"
    else:
        label = f"{final_pct}% (minimal — conditions unfavorable)"

    return final_pct, label


def _build_positions(
    thesis, spot, bias, re_gamma, acf1, pct_damp,
    call_wall, put_wall, ch_floor, ch_ceiling, ch_pos,
    ch_strategy, ch_edge, wall_break, atm_iv, dte,
    max_pain, ags, cp_ratio, charm, vanna,
    atr_dollar=0, atr_pct=0, vol_context=None,
):
    positions = []
    vc = vol_context or {}
    iv_hv = vc.get("iv_hv_ratio", 1.0)
    iv_ctx = vc.get("iv_context", "FAIR")
    skew_regime = vc.get("skew_regime", "UNKNOWN")
    vrp_ctx = vc.get("vrp_context", "FAIR")

    is_bullish = bias["direction"] == "BULLISH"
    dir_opt = "CALL" if is_bullish else "PUT"
    dir_label = "CALLS" if is_bullish else "PUTS"
    wall_target = call_wall if is_bullish else put_wall

    # Vol-aware strike offset: when IV is expensive, move strikes closer to ATM
    # because OTM options are 100% extrinsic and get crushed by vol compression.
    # When IV is cheap, OTM offers leverage with vol expansion tailwind.
    if iv_ctx in ("VERY_EXPENSIVE",) or iv_hv > 1.6:
        strike_offset = 0.005  # ATM / near-ATM (0.5% OTM max)
        vol_strike_note = "nATM strike (IV is very high — OTM options carry excessive vol risk)"
    elif iv_ctx == "EXPENSIVE" or iv_hv > 1.3:
        strike_offset = 0.01   # ~1% OTM
        vol_strike_note = "near-ATM (elevated IV — staying close reduces vol crush exposure)"
    elif iv_ctx in ("CHEAP", "SLIGHT_DISCOUNT") or iv_hv < 0.9:
        strike_offset = 0.025  # ~2.5% OTM for leverage
        vol_strike_note = "slightly OTM (IV is cheap — more leverage + potential vol expansion)"
    else:
        strike_offset = 0.015  # standard ~1.5% OTM
        vol_strike_note = ""

    strike_mult = (1 + strike_offset) if is_bullish else (1 - strike_offset)

    # Vol-aware sizing
    if iv_ctx in ("VERY_EXPENSIVE",):
        size_adj = "Reduced size — IV is very expensive, consider debit spreads"
        size_adj_channel = "Moderate — IV elevated, even with-dealer edge"
    elif iv_ctx == "EXPENSIVE":
        size_adj = "Moderate — IV is elevated, reduce exposure or use spreads"
        size_adj_channel = "Standard — with-dealer edge offsets some vol cost"
    elif iv_ctx in ("CHEAP", "SLIGHT_DISCOUNT"):
        size_adj = "Full size — IV is cheap, good environment for naked longs"
        size_adj_channel = "Full size — cheap IV + with-dealer edge"
    else:
        size_adj = None  # use thesis-specific default
        size_adj_channel = None

    # Skew-aware note for position
    skew_note = ""
    if is_bullish and skew_regime == "HIGH_PUT_SKEW":
        skew_note = " Calls have relative vol edge (put skew)."
    elif not is_bullish and skew_regime == "HIGH_PUT_SKEW":
        skew_note = " Puts are expensive (high put skew) — consider put debit spreads."
    elif is_bullish and skew_regime in ("CALL_SKEW", "EXTREME_CALL_SKEW"):
        skew_note = " Calls are expensive (call skew) — consider call debit spreads."

    # ATR-based move envelope
    max_move = _atr_max_move(atr_dollar, dte, multiplier=1.5)
    atr_note = ""
    if atr_dollar > 0:
        atr_note = f" ATR-range: ±${max_move:.0f} over {dte}d."
    if vol_strike_note:
        atr_note += f" {vol_strike_note}.{skew_note}"
    elif skew_note:
        atr_note += skew_note

    # Kelly sizing parameters
    wb_prob = wall_break.get("probability", 30)
    wall_dist_pct = abs(wall_target["strike"] - spot) / spot * 100 if wall_target.get("strike") else 5

    if thesis == "MOMENTUM_BREAKOUT":
        raw_strike = spot * strike_mult
        clamped_strike = _clamp_strike(raw_strike, spot, atr_dollar, dte, is_bullish)
        wall_reachable = abs(wall_target["strike"] - spot) <= max_move if max_move > 0 else True
        wall_str = wall_target["strike"]

        kelly_pct, kelly_label = _kelly_size(wall_dist_pct * 2, wb_prob, iv_hv, vrp_ctx)
        default_sizing = size_adj or "Full size — both Re and ACF confirm"

        positions.append({
            "name": f"BUY {dir_label} — confirmed breakout",
            "type": "momentum",
            "edge_type": "AGAINST_DEALER",
            "action": "BUY",
            "option_type": dir_opt,
            "strike": _round_strike(clamped_strike, spot),
            "dte_guidance": "5-10 DTE" if dte <= 5 else f"{dte}-{dte+5} DTE",
            "sizing": default_sizing,
            "kelly_size": kelly_label,
            "target": f"${wall_str:.0f} {'call' if is_bullish else 'put'} wall → acceleration through"
                      + ("" if wall_reachable else f" (NOTE: wall is {abs(wall_str-spot)/atr_dollar:.1f}x ATR away — may need multi-day trend)"),
            "stop": "Trail at 50% of max gain. Cut at -50% of premium.",
            "edge": f"Re={re_gamma:.1f} (turbulent) + ACF amplified. Walls are accelerators, not ceilings.{atr_note}",
            "confidence": "HIGH",
        })
        if wall_target["strike"] > 0:
            lottery_strike = _clamp_strike(wall_target["strike"], spot, atr_dollar, max(dte, 7), is_bullish)
            positions.append({
                "name": f"BUY OTM {dir_label} — wall acceleration lottery",
                "type": "lottery",
                "edge_type": "AGAINST_DEALER",
                "action": "BUY",
                "option_type": dir_opt,
                "strike": _round_strike(lottery_strike, spot),
                "dte_guidance": "7-14 DTE",
                "sizing": "Small (0.5-1% of account) — lottery ticket",
                "target": f"Wall breaks → gamma cascade beyond ${wall_str:.0f}",
                "stop": "Let ride or expire. Lottery sizing.",
                "edge": "Dealers forced to chase through the wall — mechanical acceleration.",
                "confidence": "MEDIUM" if wall_reachable else "LOW",
            })

    elif thesis == "MOMENTUM_EARLY":
        raw_strike = spot * strike_mult
        clamped_strike = _clamp_strike(raw_strike, spot, atr_dollar, dte, is_bullish)
        clamped_target = _clamp_target(wall_target["strike"], spot, atr_dollar, dte)

        kelly_pct, kelly_label = _kelly_size(wall_dist_pct * 1.5, min(wb_prob, 50), iv_hv, vrp_ctx)

        positions.append({
            "name": f"BUY {dir_label} — early momentum positioning",
            "type": "early_momentum",
            "edge_type": "AGAINST_DEALER",
            "action": "BUY",
            "option_type": dir_opt,
            "strike": _round_strike(clamped_strike, spot),
            "dte_guidance": "7-14 DTE",
            "sizing": size_adj or "Moderate — not fully confirmed yet",
            "kelly_size": kelly_label,
            "target": f"${clamped_target:.0f} {'call' if is_bullish else 'put'} wall",
            "stop": "Cut at -50% if no breakout in 5 days.",
            "edge": f"Re={re_gamma:.1f} says dealers are overwhelmed — ACF should follow.{atr_note}",
            "confidence": "MEDIUM",
        })

    elif thesis == "MOMENTUM_TREND":
        trend_strike = spot * strike_mult
        clamped_trend = _clamp_strike(trend_strike, spot, atr_dollar, dte, is_bullish)
        kelly_pct, kelly_label = _kelly_size(atr_pct * 2, 55, iv_hv, vrp_ctx)
        positions.append({
            "name": f"BUY {dir_label} — ride the trend",
            "type": "momentum",
            "edge_type": "AGAINST_DEALER",
            "action": "BUY",
            "option_type": dir_opt,
            "strike": _round_strike(clamped_trend, spot),
            "dte_guidance": "5-10 DTE",
            "sizing": size_adj or "Standard — ACF confirms trend",
            "kelly_size": kelly_label,
            "target": "Ride until ACF flips or move stalls",
            "stop": "Trail at 40% of max gain. Cut if trend reverses intraday.",
            "edge": f"ACF={acf1:+.3f} → moves follow through. Don't fade.{atr_note}",
            "confidence": "MEDIUM",
        })

    elif thesis == "CONFLICTED_PIN":
        cw_strike = call_wall["strike"]
        cw_reachable = abs(cw_strike - spot) <= max_move if max_move > 0 else True
        # Fade target: use 1-2x ATR pullback (realistic for a pin/fade), not a fixed 3%
        if atr_dollar > 0:
            fade_target = _round_strike(spot - atr_dollar * 1.5, spot)
        else:
            fade_target = _round_strike(spot * 0.985, spot)

        positions.append({
            "name": "BUY CALLS — ride TO the gamma wall (not through it)",
            "type": "pin_approach",
            "edge_type": "WITH_DEALER",
            "action": "BUY",
            "option_type": "CALL",
            "strike": _round_strike(spot, spot),
            "dte_guidance": "3-7 DTE",
            "sizing": "Standard",
            "target": f"${cw_strike:.0f} call wall as destination (not breakout)"
                      + ("" if cw_reachable else f" (NOTE: {abs(cw_strike-spot)/atr_dollar:.1f}x ATR away)"),
            "stop": "Take profit AT the wall. Do NOT hold through.",
            "edge": f"Re={re_gamma:.2f} (laminar) → walls are magnets, not breakout triggers.{atr_note}",
            "confidence": "MEDIUM" if cw_reachable else "LOW",
        })
        if cw_strike > 0:
            positions.append({
                "name": "BUY PUTS at call wall — fade the rejection",
                "type": "wall_fade",
                "edge_type": "WITH_DEALER",
                "action": "BUY",
                "option_type": "PUT",
                "strike": _round_strike(cw_strike, spot),
                "dte_guidance": "3-7 DTE",
                "sizing": "Standard — WAIT for price to reach wall first",
                "target": f"Wall rejects → pullback to ${_round_strike(fade_target, spot):.0f}",
                "stop": "Quick trade — 1-2 days. Cut if wall breaks by >1%.",
                "edge": f"Wall-break probability only {wall_break['probability']}%. Fade the touch.",
                "confidence": "MEDIUM",
            })

    elif thesis in ("FADE_MOVES", "FADE_MILD"):
        fade_call_offset = 0.005 if iv_hv > 1.3 else 0.01
        fade_put_offset = 0.005 if iv_hv > 1.3 else 0.01
        fade_call_strike = _round_strike(spot * (1 - fade_call_offset), spot)
        fade_put_strike = _round_strike(spot * (1 + fade_put_offset), spot)

        fade_call_sizing = size_adj_channel or "Standard — high-frequency setup"
        fade_put_sizing = size_adj_channel or "Standard"

        fade_call_edge = f"ACF={acf1:+.3f} → {pct_damp:.0f}% of days are dampened. Moves reverse.{atr_note}"
        fade_put_edge = f"Dampened regime = mean-reversion. Yesterday's winners are today's losers.{atr_note}"

        fade_win = 65 if thesis == "FADE_MOVES" else 55
        kelly_pct, kelly_label = _kelly_size(atr_pct, fade_win, iv_hv, vrp_ctx)

        positions.append({
            "name": "BUY CALLS after a red day (fade the dip)",
            "type": "fade_dip",
            "edge_type": "WITH_DEALER",
            "action": "BUY",
            "option_type": "CALL",
            "strike": fade_call_strike,
            "dte_guidance": "3-7 DTE",
            "sizing": fade_call_sizing,
            "kelly_size": kelly_label,
            "target": f"Snap-back to prior close / ${ags:.0f} gamma strike",
            "stop": "Take profit at 30-50% gain. Cut at -40%. 1-2 day hold.",
            "edge": fade_call_edge,
            "confidence": "HIGH" if thesis == "FADE_MOVES" else "MEDIUM",
        })
        positions.append({
            "name": "BUY PUTS after a green day (fade the rip)",
            "type": "fade_rip",
            "edge_type": "WITH_DEALER",
            "action": "BUY",
            "option_type": "PUT",
            "strike": fade_put_strike,
            "dte_guidance": "3-7 DTE",
            "sizing": fade_put_sizing,
            "kelly_size": kelly_label,
            "target": f"Pullback to prior close / ${ags:.0f} gamma strike",
            "stop": "Take profit at 30-50% gain. Cut at -40%. 1-2 day hold.",
            "edge": fade_put_edge,
            "confidence": "HIGH" if thesis == "FADE_MOVES" else "MEDIUM",
        })

    # ---- WITH-DEALER Channel Strategies (available most days) ----
    if ch_strategy == "GEX_FLOOR_BOUNCE" and ch_floor:
        mid_ch = (ch_floor + ch_ceiling) / 2 if ch_ceiling else spot
        mid_ch_clamped = _clamp_target(mid_ch, spot, atr_dollar, dte) if atr_dollar > 0 else mid_ch
        ch_move_pct = abs(mid_ch_clamped - spot) / spot * 100 if spot > 0 else 1
        kelly_pct, kelly_label = _kelly_size(ch_move_pct, 70, iv_hv, vrp_ctx)
        positions.append({
            "name": f"BUY CALLS — GEX Floor Bounce at ${ch_floor:.0f}",
            "type": "gex_floor_bounce",
            "edge_type": "WITH_DEALER",
            "action": "BUY",
            "option_type": "CALL",
            "strike": _round_strike(spot, spot),
            "dte_guidance": "1-5 DTE",
            "sizing": size_adj_channel or "Full size — WITH dealer, high-probability",
            "kelly_size": kelly_label,
            "target": f"Mid-channel: ${mid_ch_clamped:.0f}",
            "stop": "Stop if floor breaks by >1%. Take profit at mid-channel.",
            "edge": f"Dealers LONG gamma = they BUY dips at ${ch_floor:.0f}. You're alongside them.{atr_note}",
            "confidence": "HIGH",
        })

    if ch_strategy == "GEX_CEILING_FADE" and ch_ceiling:
        mid_ch = (ch_floor + ch_ceiling) / 2 if ch_floor else spot
        mid_ch_clamped = _clamp_target(mid_ch, spot, atr_dollar, dte) if atr_dollar > 0 else mid_ch
        ch_move_pct = abs(mid_ch_clamped - spot) / spot * 100 if spot > 0 else 1
        kelly_pct, kelly_label = _kelly_size(ch_move_pct, 70, iv_hv, vrp_ctx)
        positions.append({
            "name": f"BUY PUTS — GEX Ceiling Fade at ${ch_ceiling:.0f}",
            "type": "gex_ceiling_fade",
            "edge_type": "WITH_DEALER",
            "action": "BUY",
            "option_type": "PUT",
            "strike": _round_strike(spot, spot),
            "dte_guidance": "1-5 DTE",
            "sizing": size_adj_channel or "Full size — WITH dealer, high-probability",
            "kelly_size": kelly_label,
            "target": f"Mid-channel: ${mid_ch_clamped:.0f}",
            "stop": "Stop if ceiling breaks by >1%. Take profit at mid-channel.",
            "edge": f"Dealers LONG gamma = they SELL rips at ${ch_ceiling:.0f}. You're alongside them.{atr_note}",
            "confidence": "HIGH",
        })

    # ---- Charm/Vanna Drift (short DTE) ----
    if dte <= 5 and abs(charm) > 10000:
        drift_dir = "BEARISH" if spot > max_pain else "BULLISH"
        drift_opt = "PUT" if drift_dir == "BEARISH" else "CALL"
        positions.append({
            "name": f"BUY {drift_opt}S — Charm/Vanna Drift toward ${max_pain:.0f}",
            "type": "charm_drift",
            "edge_type": "WITH_DEALER",
            "action": "BUY",
            "option_type": drift_opt,
            "strike": _round_strike(spot, spot),
            "dte_guidance": "0-3 DTE",
            "sizing": "Small — timing-sensitive play",
            "target": f"${max_pain:.0f} max pain ({abs(spot - max_pain) / spot * 100:.1f}% away)",
            "stop": "Close by end of day before expiry.",
            "edge": f"Charm={charm:,.0f}, Vanna={vanna:,.0f} → mechanical drift toward dealer's optimal pin.",
            "confidence": "MEDIUM",
        })

    # ---- Neutral: wait or lottery ----
    if thesis == "NEUTRAL":
        positions.append({
            "name": "NO POSITION — wait for signal",
            "type": "skip",
            "edge_type": "NEUTRAL",
            "action": "WAIT",
            "option_type": "—",
            "strike": 0,
            "dte_guidance": "—",
            "sizing": "Flat",
            "target": "Wait for ACF to move past +/-0.05",
            "stop": "—",
            "edge": "Staying flat when there's no edge IS the edge.",
            "confidence": "N/A",
        })

    return positions


def _build_level_actions(
    spot, thesis, re_regime, gex_regime,
    max_pain, call_wall, put_wall, flip_point,
    ags, ch_floor, ch_ceiling, wall_break,
    vwap_level=None, atr_dollar=0, dte=0,
):
    """
    For each key level, describe what to EXPECT and what to DO when price gets there.
    This is the core "guide me through the trade" feature.
    """
    actions = []
    is_laminar = re_regime == "LAMINAR"
    wb_prob = wall_break.get("probability", 15)

    # Max Pain
    if max_pain > 0:
        dist_pct = round(abs(spot - max_pain) / spot * 100, 2)
        if spot > max_pain:
            actions.append({
                "level": max_pain,
                "label": "Max Pain",
                "type": "max_pain",
                "distance_pct": dist_pct,
                "side": "below",
                "expectation": "Price is pulled toward this level as charm/vanna flows force dealer re-hedging. Gravitational center.",
                "action": f"BUY PUTS if above — expect gradual drift down. Or wait for arrival and BUY CALLS for the bounce.",
                "watch_for": "Drift accelerates in final 3 DTE. Strongest signal post-IV-event.",
            })
        else:
            actions.append({
                "level": max_pain,
                "label": "Max Pain",
                "type": "max_pain",
                "distance_pct": dist_pct,
                "side": "above",
                "expectation": "Price gravitates up toward max pain. Dealers benefit from expiry here.",
                "action": f"BUY CALLS — expect drift upward. Strongest in calm markets near expiry.",
                "watch_for": "Any catalyst can override max pain gravity. Works best with no events.",
            })

    # Call Wall
    if call_wall.get("strike", 0) > 0:
        cw = call_wall["strike"]
        dist_pct = round(abs(spot - cw) / spot * 100, 2)
        if is_laminar:
            actions.append({
                "level": cw,
                "label": "Call Wall (Resistance)",
                "type": "call_wall",
                "distance_pct": dist_pct,
                "side": "above",
                "expectation": f"EXPECT REVERSAL. Dealers sell into this level. {wb_prob}% chance it breaks.",
                "action": f"BUY PUTS when price reaches ${cw:.0f}. Fade the rejection.",
                "watch_for": f"If Re crosses 1.0, the wall becomes an ACCELERATOR instead. Monitor Re.",
            })
        else:
            actions.append({
                "level": cw,
                "label": "Call Wall (Breakout Trigger)",
                "type": "call_wall",
                "distance_pct": dist_pct,
                "side": "above",
                "expectation": f"EXPECT BREAKOUT. Dealers are short gamma — wall becomes fuel. {wb_prob}% break probability.",
                "action": f"BUY CALLS targeting ${cw:.0f}. If it breaks, ADD — acceleration follows.",
                "watch_for": "Volume surge at the wall confirms breakout. Fake breakout if volume is thin.",
            })

    # Put Wall
    if put_wall.get("strike", 0) > 0:
        pw = put_wall["strike"]
        dist_pct = round(abs(spot - pw) / spot * 100, 2)
        if is_laminar:
            actions.append({
                "level": pw,
                "label": "Put Wall (Support)",
                "type": "put_wall",
                "distance_pct": dist_pct,
                "side": "below",
                "expectation": f"EXPECT BOUNCE. Dealers buy into this level. Strong support.",
                "action": f"BUY CALLS when price reaches ${pw:.0f}. Trade WITH the dealer's buying.",
                "watch_for": "If it breaks, support becomes a trapdoor. Cut quickly.",
            })
        else:
            actions.append({
                "level": pw,
                "label": "Put Wall (Breakdown Trigger)",
                "type": "put_wall",
                "distance_pct": dist_pct,
                "side": "below",
                "expectation": f"EXPECT BREAKDOWN if reached. Dealers amplify the selling.",
                "action": f"BUY PUTS if price approaches ${pw:.0f}. Break triggers acceleration lower.",
                "watch_for": "This level becomes a waterfall trigger in negative gamma.",
            })

    # Flip Point
    if flip_point and flip_point > 0:
        dist_pct = round(abs(spot - flip_point) / spot * 100, 2)
        side = "below" if flip_point < spot else "above"
        actions.append({
            "level": flip_point,
            "label": "GEX Flip Point (Regime Change)",
            "type": "flip_point",
            "distance_pct": dist_pct,
            "side": side,
            "expectation": "REGIME CHANGE TRIGGER. Above = dealers stabilize. Below = dealers amplify. Everything changes here.",
            "action": f"If price crosses ${flip_point:.0f}: BUY PUTS on break below (dealers start selling with you), BUY CALLS on reclaim above.",
            "watch_for": "Require a CLOSING price confirmation. Intraday wicks through are not reliable.",
        })

    # GEX Channel Floor
    if ch_floor and ch_floor > 0:
        dist_pct = round(abs(spot - ch_floor) / spot * 100, 2)
        actions.append({
            "level": ch_floor,
            "label": "GEX Channel Floor",
            "type": "channel_floor",
            "distance_pct": dist_pct,
            "side": "below",
            "expectation": "Dealer buying zone. Long gamma means they BUY dips here mechanically.",
            "action": f"BUY CALLS at ${ch_floor:.0f}. You're trading alongside the dealer's forced buying.",
            "watch_for": "Floor only holds in long gamma / laminar regime. Check Re before relying on it.",
        })

    # GEX Channel Ceiling
    if ch_ceiling and ch_ceiling > 0:
        dist_pct = round(abs(spot - ch_ceiling) / spot * 100, 2)
        actions.append({
            "level": ch_ceiling,
            "label": "GEX Channel Ceiling",
            "type": "channel_ceiling",
            "distance_pct": dist_pct,
            "side": "above",
            "expectation": "Dealer selling zone. Long gamma means they SELL rips here mechanically.",
            "action": f"BUY PUTS at ${ch_ceiling:.0f}. Dealers will sell alongside you.",
            "watch_for": "Ceiling breaks in turbulent regime. Only fade in laminar conditions.",
        })

    # Absolute Gamma Strike
    if ags and ags > 0:
        dist_pct = round(abs(spot - ags) / spot * 100, 2)
        side = "below" if ags < spot else "above"
        actions.append({
            "level": ags,
            "label": "Absolute Gamma Strike (Magnet)",
            "type": "abs_gamma_strike",
            "distance_pct": dist_pct,
            "side": side,
            "expectation": "Strongest gamma concentration. Price is magnetically attracted here in final days.",
            "action": f"In final 3-5 DTE, expect pinning near ${ags:.0f}. Trade toward it, not away.",
            "watch_for": "Pinning effect strongest within 0.5% of this strike in the last 3 days.",
        })

    # VWAP
    if vwap_level and vwap_level > 0:
        dist_pct = round(abs(spot - vwap_level) / spot * 100, 2)
        side = "below" if vwap_level < spot else "above"
        if spot > vwap_level:
            actions.append({
                "level": vwap_level,
                "label": "VWAP (Institutional Fair Value)",
                "type": "vwap",
                "distance_pct": dist_pct,
                "side": side,
                "expectation": "Volume-weighted average price — institutional mean-reversion target. Dips to VWAP tend to find buyers.",
                "action": f"BUY CALLS on dip to ${vwap_level:.0f}. Institutions treat VWAP as fair value — expect buying interest.",
                "watch_for": "If price slices through VWAP on heavy volume, it becomes resistance instead of support.",
            })
        else:
            actions.append({
                "level": vwap_level,
                "label": "VWAP (Institutional Fair Value)",
                "type": "vwap",
                "distance_pct": dist_pct,
                "side": side,
                "expectation": "Below VWAP — sellers in control. Rallies into VWAP face institutional selling pressure.",
                "action": f"BUY PUTS on rally to ${vwap_level:.0f}. Or BUY CALLS if price reclaims VWAP on strong volume.",
                "watch_for": "VWAP reclaim on volume = regime shift from seller to buyer control.",
            })

    # Enrich each action with collision probability (Ducournau)
    for a in actions:
        p_col = _collision_probability(spot, a["level"], atr_dollar, dte)
        a["collision_prob"] = round(p_col * 100, 1)
        a["collision_label"] = "LIKELY" if p_col > 0.6 else "POSSIBLE" if p_col > 0.3 else "UNLIKELY"

    # Consolidate actions at the same price level to reduce noise
    actions = _consolidate_level_actions(actions, spot)
    actions.sort(key=lambda a: a["distance_pct"])
    return actions


def _consolidate_level_actions(actions, spot):
    """
    When multiple level types land on the same strike (e.g. call wall,
    channel ceiling, and AGS all at $51), merge them into a single
    composite entry instead of showing 3 near-identical cards.
    """
    threshold_pct = 0.3  # strikes within 0.3% of each other are "the same"

    groups = []
    used = set()
    for i, a in enumerate(actions):
        if i in used:
            continue
        group = [a]
        used.add(i)
        for j, b in enumerate(actions):
            if j in used:
                continue
            if abs(a["level"] - b["level"]) / spot * 100 < threshold_pct:
                group.append(b)
                used.add(j)
        groups.append(group)

    merged = []
    for group in groups:
        if len(group) == 1:
            merged.append(group[0])
            continue

        # Pick the most important type as primary
        type_priority = {
            "flip_point": 0, "call_wall": 1, "put_wall": 2,
            "max_pain": 3, "abs_gamma_strike": 4,
            "channel_floor": 5, "channel_ceiling": 6,
        }
        group.sort(key=lambda a: type_priority.get(a["type"], 99))
        primary = group[0].copy()

        other_labels = [a["label"] for a in group[1:]]
        primary["label"] += " + " + " + ".join(other_labels)

        extra_insights = []
        for a in group[1:]:
            if a.get("expectation") and a["expectation"] != primary["expectation"]:
                extra_insights.append(a["expectation"])
        if extra_insights:
            primary["expectation"] += " Additionally: " + " ".join(extra_insights)

        merged.append(primary)

    return merged


def _build_avoid_list(thesis, phase_regime):
    avoid = []
    if thesis in ("MOMENTUM_BREAKOUT", "MOMENTUM_TREND", "MOMENTUM_EARLY"):
        avoid.append("Do NOT fade the move — ACF says moves follow through")
        avoid.append("Do NOT sell premium — you'll get run over in momentum regimes")
    if thesis in ("FADE_MOVES", "FADE_MILD"):
        avoid.append("Do NOT chase breakouts — they're more likely to reverse")
        avoid.append("Do NOT hold fades too long — take profit in 1-2 days")
    if thesis == "CONFLICTED_PIN":
        avoid.append("Do NOT assume walls will break — Re says dealers have capacity")
        avoid.append("Do NOT hold calls through the wall — take profit AT the wall")
    if thesis == "NEUTRAL":
        avoid.append("Do NOT force a trade — no edge means no trade")
    if phase_regime == "TURBULENT":
        avoid.append("Do NOT undersize — phase transitions move fast")
    return avoid


def _collision_probability(spot, level, atr_dollar, dte):
    """
    Ducournau-inspired: probability that price reaches 'level' within DTE,
    given ATR-derived expected move envelope.
    """
    if atr_dollar <= 0 or dte <= 0 or level <= 0:
        return 0.5
    distance = abs(spot - level)
    if distance < 0.01:
        return 0.95
    max_move = atr_dollar * np.sqrt(dte)
    return min(0.95, max(0.05, max_move / (2 * distance)))


def _estimate_wall_break_probability(
    re_gamma, re_regime, acf1, pct_amp, pct_damp, phase_regime, cp_ratio,
    beta_adj=1.0, atr_dollar=0, dte=0, call_wall_strike=0, put_wall_strike=0,
    spot=0, sei=0, entropy_regime="DISPERSED", gex_regime="POSITIVE_GAMMA",
):
    prob = 15.0

    # Beta-adjusted Reynolds thresholds
    re_adj = re_gamma * beta_adj
    if re_adj > 2.0:
        prob += 45
    elif re_adj > 1.0:
        prob += 30
    elif re_adj > 0.7:
        prob += 15
    elif re_adj > 0.3:
        prob += 5

    # Asymmetric Gamma Effect (Bakshi/Ducournau):
    # Positive dealer gamma dampens moves ~2.5x more effectively than
    # negative gamma amplifies them. When dealers are long gamma at
    # current price, walls are MUCH harder to break. When short gamma,
    # the amplification is real but weaker than the dampening.
    if gex_regime == "POSITIVE_GAMMA":
        gamma_asymmetry = -12  # strong dampening penalty
    else:
        gamma_asymmetry = 5   # mild amplification boost
    prob += gamma_asymmetry

    if acf1 > 0.10:
        prob += 10
    elif acf1 > 0.05:
        prob += 5
    elif acf1 < -0.15:
        prob -= 10
    elif acf1 < -0.05:
        prob -= 5

    if phase_regime == "TURBULENT":
        prob += 10
    elif phase_regime == "APPROACHING":
        prob += 5

    # Self-excitation boost
    if sei > 150:
        prob += 8
    elif sei > 80:
        prob += 4

    # GEX entropy boost — concentrated gamma = walls more vulnerable
    if entropy_regime == "CRITICAL":
        prob += 10
    elif entropy_regime == "APPROACHING":
        prob += 5

    # Ducournau collision probability refinement:
    # adjust based on odds of actually reaching the nearest wall
    p_cw = _collision_probability(spot, call_wall_strike, atr_dollar, dte) if call_wall_strike > 0 and spot > 0 else 0.5
    p_pw = _collision_probability(spot, put_wall_strike, atr_dollar, dte) if put_wall_strike > 0 and spot > 0 else 0.5
    p_nearest = max(p_cw, p_pw)
    if p_nearest > 0.05 and p_nearest < 0.95:
        odds = p_nearest / (1 - p_nearest)
        collision_adj = (1 + np.log1p(odds)) / 2
        prob *= collision_adj

    prob = max(5, min(95, prob))

    re_says = "BREAK" if re_adj > 1.0 else "HOLD"
    acf_says = "BREAK" if (pct_amp > pct_damp and acf1 > 0) or acf1 > 0.10 else "HOLD"
    sei_says = "BREAK" if sei > 150 else "HOLD"

    # Confidence accounts for all three signals
    agree_break = sum(1 for s in [re_says, acf_says, sei_says] if s == "BREAK")
    agree_hold = sum(1 for s in [re_says, acf_says, sei_says] if s == "HOLD")

    if agree_break >= 2 and re_says == "BREAK":
        confidence = "HIGH"
        explanation = f"Reynolds (beta-adj) + {'ACF' if acf_says == 'BREAK' else 'SEI'} agree: walls should break"
    elif agree_hold >= 2 and re_says == "HOLD":
        confidence = "HIGH"
        explanation = "Reynolds + ACF agree: walls hold (expect reversal)"
    elif re_says == "BREAK":
        confidence = "MEDIUM"
        explanation = "Reynolds says break, waiting for ACF/SEI confirmation — early stage"
    elif acf_says == "BREAK" or sei_says == "BREAK":
        confidence = "LOW"
        explanation = "Flow signals suggest break but Reynolds says dealers still in control"
    else:
        confidence = "HIGH"
        explanation = "All signals agree: walls hold (expect reversal)"

    return {
        "probability": round(prob),
        "confidence": confidence,
        "explanation": explanation,
        "re_says": re_says,
        "acf_says": acf_says,
        "sei_says": sei_says,
        "gamma_asymmetry": gamma_asymmetry,
        "collision_prob_call_wall": round(p_cw * 100, 1),
        "collision_prob_put_wall": round(p_pw * 100, 1),
        "beta_adj_factor": round(beta_adj, 2),
        "re_beta_adj": round(re_adj, 2),
    }


def _thesis_label(thesis):
    labels = {
        "MOMENTUM_BREAKOUT": "Confirmed Breakout — Ride It",
        "MOMENTUM_EARLY": "Early Momentum — Position Before Confirmation",
        "MOMENTUM_TREND": "Trending — Ride the Direction",
        "CONFLICTED_PIN": "Walls Hold — Trade TO Walls, Not Through",
        "FADE_MOVES": "Strong Mean-Reversion — Fade Every Move",
        "FADE_MILD": "Mild Dampening — Fade Cautiously",
        "NEUTRAL": "No Clear Edge — Wait or Lottery Only",
    }
    return labels.get(thesis, thesis)
