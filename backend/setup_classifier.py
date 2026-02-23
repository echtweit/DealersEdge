"""
Setup Classifier — the brain of the framework.
Classifies current conditions into one of four dealer-exploiting setups
and generates trade guidance with specific structure recommendations.
"""
from datetime import datetime, timedelta


SETUP_CONFIGS = {
    "PIN": {
        "name": "The Pin Trade",
        "icon": "pin",
        "dte_range": (5, 15),
        "description": "Price gravitates toward max pain / high-gamma strike as dealer hedging stabilizes.",
        "thesis": "Dealer is net short gamma near ATM. Every move away from the pin strike forces re-hedging back toward it. The dealer IS the rubber band.",
        "structure": "Short iron condor or short strangle centered at the absolute gamma strike. Short strikes at call/put walls.",
        "greeks_wanted": "High positive theta, short vega (enter on elevated IV), net short gamma.",
        "exit_rules": "Close at 50% of max credit received, or at 2 DTE — whichever comes first. Never hold through expiry.",
        "risk": "Any surprise catalyst breaks the pin. Always define max loss.",
        "regime_required": "POSITIVE_GAMMA",
    },
    "WALL_FADE": {
        "name": "The Wall Fade",
        "icon": "wall",
        "dte_range": (10, 20),
        "description": "Price approaches a Call or Put Wall and dealer hedging creates real support/resistance.",
        "thesis": "As price hits a high-gamma wall, dealer hedging acts against the move — selling into call walls, buying into put walls.",
        "structure": "Credit spread at the wall. Sell call spread with short leg at Call Wall, or put spread with short leg at Put Wall.",
        "greeks_wanted": "Short delta (slight), short vega, heavy theta. Enter when IV is elevated.",
        "exit_rules": "Close at 50% profit or when wall OI dissolves (check daily OI changes).",
        "risk": "Wall can be breached on high volume / catalyst. Size accordingly.",
        "regime_required": "POSITIVE_GAMMA",
    },
    "FLIP": {
        "name": "The GEX Flip Breakout",
        "icon": "flip",
        "dte_range": (5, 20),
        "description": "Price breaks through the GEX Flip Point — regime change triggers dealer amplification.",
        "thesis": "Crossing the flip point shifts dealers from stabilizing to amplifying. Every 1% move forces more hedging in the same direction — a feedback loop.",
        "structure": "Long puts (break below flip) or long calls (reclaim above flip). Debit spreads to cap cost.",
        "greeks_wanted": "Long delta in breakout direction, long gamma (want acceleration), accept negative theta. Long vega is a bonus.",
        "exit_rules": "Target 100% on long options, 75-80% on debit spreads. Hard stop if flip point is reclaimed.",
        "risk": "False breakouts. Require a closing price confirmation, not just an intraday wick.",
        "regime_required": "NEGATIVE_GAMMA",
    },
    "VANNA_DRIFT": {
        "name": "The Vanna/Charm Drift",
        "icon": "drift",
        "dte_range": (2, 7),
        "description": "Post-IV-event: charm and vanna flows pull price toward the highest OI cluster.",
        "thesis": "After IV crush, delta changes via vanna force dealer re-hedging. Price drifts mechanically toward the strike the dealer most wants to pin.",
        "structure": "Tight debit spread in the direction of the nearest large OI cluster. Very short duration.",
        "greeks_wanted": "Long delta toward the OI magnet, short vega (IV already crushed). Pure delta play with vanna tailwind.",
        "exit_rules": "Close by end of day before expiry, or when price reaches the OI magnet strike.",
        "risk": "Works best within 1-3 DTE. Macro noise can interfere at longer durations.",
        "regime_required": "ANY",
    },
}


def classify_setup(
    spot: float,
    dte: int,
    regime: str,
    flip_point: float,
    max_pain: float,
    call_wall: dict,
    put_wall: dict,
    abs_gamma_strike: float,
    total_charm: float,
    total_vanna: float,
    iv_rank: float = None,
) -> list[dict]:
    """
    Classify current market conditions into applicable setups.
    Returns a ranked list of setups with confidence scores and trade guidance.
    """
    setups = []
    distance_to_max_pain_pct = abs(spot - max_pain) / spot * 100
    distance_to_flip_pct = abs(spot - flip_point) / spot * 100 if flip_point else 999
    distance_to_ags_pct = abs(spot - abs_gamma_strike) / spot * 100
    distance_to_call_wall_pct = abs(spot - call_wall["strike"]) / spot * 100 if call_wall["strike"] else 999
    distance_to_put_wall_pct = abs(spot - put_wall["strike"]) / spot * 100 if put_wall["strike"] else 999

    # --- Setup 1: The Pin Trade ---
    if regime == "POSITIVE_GAMMA" and SETUP_CONFIGS["PIN"]["dte_range"][0] <= dte <= SETUP_CONFIGS["PIN"]["dte_range"][1]:
        confidence = 0
        signals = []

        if distance_to_max_pain_pct <= 1.5:
            confidence += 35
            signals.append(f"Price within {distance_to_max_pain_pct:.1f}% of max pain ({max_pain})")
        elif distance_to_max_pain_pct <= 3.0:
            confidence += 15
            signals.append(f"Price moderately close to max pain ({distance_to_max_pain_pct:.1f}%)")

        if distance_to_ags_pct <= 1.0:
            confidence += 25
            signals.append(f"Price within {distance_to_ags_pct:.1f}% of absolute gamma strike ({abs_gamma_strike})")

        if regime == "POSITIVE_GAMMA":
            confidence += 20
            signals.append("Positive gamma regime — dealers stabilizing price")

        if total_charm < 0:
            confidence += 10
            signals.append("Negative charm — quiet daily bid from delta decay re-hedging")

        if dte <= 10:
            confidence += 10
            signals.append(f"Short DTE ({dte}) — theta acceleration working in your favor")

        if confidence >= 30:
            target_strike = abs_gamma_strike if distance_to_ags_pct < distance_to_max_pain_pct else max_pain
            setups.append(_build_setup("PIN", confidence, signals, {
                "center_strike": target_strike,
                "upper_wing": call_wall["strike"],
                "lower_wing": put_wall["strike"],
                "direction": "neutral",
            }))

    # --- Setup 2: The Wall Fade ---
    if regime == "POSITIVE_GAMMA" and SETUP_CONFIGS["WALL_FADE"]["dte_range"][0] <= dte <= SETUP_CONFIGS["WALL_FADE"]["dte_range"][1]:
        # Check call wall approach
        if distance_to_call_wall_pct <= 2.0 and spot < call_wall["strike"]:
            confidence = 30
            signals = [f"Price approaching Call Wall at {call_wall['strike']} (OI: {call_wall['oi']:,})"]
            if call_wall["oi"] > 5000:
                confidence += 20
                signals.append("Heavy call OI — strong dealer resistance")
            if regime == "POSITIVE_GAMMA":
                confidence += 15
                signals.append("Positive gamma — dealer hedging pushes back against price")
            if distance_to_call_wall_pct <= 0.5:
                confidence += 15
                signals.append("Very close to wall — reversal imminent")

            if confidence >= 40:
                setups.append(_build_setup("WALL_FADE", confidence, signals, {
                    "wall_type": "CALL",
                    "wall_strike": call_wall["strike"],
                    "wall_oi": call_wall["oi"],
                    "direction": "bearish",
                    "short_strike": call_wall["strike"],
                }))

        # Check put wall approach
        if distance_to_put_wall_pct <= 2.0 and spot > put_wall["strike"]:
            confidence = 30
            signals = [f"Price approaching Put Wall at {put_wall['strike']} (OI: {put_wall['oi']:,})"]
            if put_wall["oi"] > 5000:
                confidence += 20
                signals.append("Heavy put OI — strong dealer support")
            if regime == "POSITIVE_GAMMA":
                confidence += 15
                signals.append("Positive gamma — dealer hedging creates floor")
            if distance_to_put_wall_pct <= 0.5:
                confidence += 15
                signals.append("Very close to wall — bounce imminent")

            if confidence >= 40:
                setups.append(_build_setup("WALL_FADE", confidence, signals, {
                    "wall_type": "PUT",
                    "wall_strike": put_wall["strike"],
                    "wall_oi": put_wall["oi"],
                    "direction": "bullish",
                    "short_strike": put_wall["strike"],
                }))

    # --- Setup 3: The GEX Flip Breakout ---
    if flip_point and SETUP_CONFIGS["FLIP"]["dte_range"][0] <= dte <= SETUP_CONFIGS["FLIP"]["dte_range"][1]:
        confidence = 0
        signals = []

        if regime == "NEGATIVE_GAMMA":
            confidence += 30
            signals.append("NEGATIVE gamma regime — dealers amplifying moves")

            if distance_to_flip_pct <= 0.5:
                confidence += 20
                signals.append(f"Just broke through flip point ({flip_point}) — fresh regime change")
            elif distance_to_flip_pct <= 2.0:
                confidence += 10
                signals.append(f"Below flip point by {distance_to_flip_pct:.1f}%")

            direction = "bearish" if spot < flip_point else "bullish"
            confidence += 15
            signals.append(f"Directional bias: {direction} — dealer feedback loop active")

            if confidence >= 40:
                setups.append(_build_setup("FLIP", confidence, signals, {
                    "flip_point": flip_point,
                    "direction": direction,
                    "distance_to_flip": round(distance_to_flip_pct, 2),
                }))

    # --- Setup 4: The Vanna/Charm Drift ---
    if SETUP_CONFIGS["VANNA_DRIFT"]["dte_range"][0] <= dte <= SETUP_CONFIGS["VANNA_DRIFT"]["dte_range"][1]:
        confidence = 0
        signals = []

        if abs(total_vanna) > 0:
            confidence += 15
            signals.append(f"Net vanna exposure: {total_vanna:,.0f}")

        if total_charm < 0:
            confidence += 20
            signals.append("Negative charm — time-driven delta decay forcing re-hedging")

        if distance_to_max_pain_pct <= 3.0 and dte <= 5:
            confidence += 25
            signals.append(f"Max pain ({max_pain}) within {distance_to_max_pain_pct:.1f}% — drift target identified")

        if dte <= 3:
            confidence += 15
            signals.append(f"Ultra-short DTE ({dte}) — charm/vanna flows are dominant force")

        # Determine drift direction
        if spot > max_pain:
            direction = "bearish"
            drift_target = max_pain
        else:
            direction = "bullish"
            drift_target = max_pain

        if confidence >= 30:
            setups.append(_build_setup("VANNA_DRIFT", confidence, signals, {
                "drift_target": drift_target,
                "direction": direction,
                "charm_exposure": total_charm,
                "vanna_exposure": total_vanna,
            }))

    setups.sort(key=lambda x: x["confidence"], reverse=True)
    return setups


def _build_setup(setup_type: str, confidence: int, signals: list[str], trade_params: dict) -> dict:
    config = SETUP_CONFIGS[setup_type]
    confidence = min(confidence, 100)

    return {
        "type": setup_type,
        "name": config["name"],
        "icon": config["icon"],
        "confidence": confidence,
        "confidence_label": _confidence_label(confidence),
        "description": config["description"],
        "thesis": config["thesis"],
        "signals": signals,
        "structure": config["structure"],
        "greeks_wanted": config["greeks_wanted"],
        "exit_rules": config["exit_rules"],
        "risk": config["risk"],
        "trade_params": trade_params,
    }


def _confidence_label(score: int) -> str:
    if score >= 75:
        return "HIGH"
    if score >= 50:
        return "MEDIUM"
    if score >= 30:
        return "LOW"
    return "SPECULATIVE"


def get_risk_guidance(spot: float, setup: dict, account_size: float = None) -> dict:
    """Generate position sizing and risk management guidance."""
    guidance = {
        "max_loss_pct": 2.0,
        "stop_rules": [],
        "warnings": [],
    }

    setup_type = setup["type"]

    if setup_type in ("PIN", "WALL_FADE"):
        guidance["stop_rules"].append("Close at 2x credit received for a loss")
        guidance["stop_rules"].append("Close at 50% credit received for a profit")
        guidance["stop_rules"].append("Close at 2 DTE regardless of P&L")
        guidance["warnings"].append("Never hold short options through expiry — pin risk cuts both ways")
    elif setup_type == "FLIP":
        guidance["stop_rules"].append("Hard stop if flip point is reclaimed on a closing basis")
        guidance["stop_rules"].append("Close at 50% loss on debit")
        guidance["stop_rules"].append("Target 75-100% gain on debit spreads")
        guidance["warnings"].append("Require closing price confirmation — not intraday wicks")
    elif setup_type == "VANNA_DRIFT":
        guidance["stop_rules"].append("Close by end of day before expiry")
        guidance["stop_rules"].append("Close when price reaches the drift target strike")
        guidance["warnings"].append("This is a 1-3 day trade max — don't hold longer")

    guidance["warnings"].append("Multiple positions on the same expiry are correlated — treat as one position for sizing")

    if account_size:
        max_risk = account_size * (guidance["max_loss_pct"] / 100)
        guidance["max_risk_dollars"] = round(max_risk, 2)
        guidance["sizing_note"] = f"Max ${max_risk:,.0f} at risk per expiry ({guidance['max_loss_pct']}% of ${account_size:,.0f})"

    return guidance
