"""
Gamma Channel — trade WITH the dealer.
Defines the floor (dealers buy dips) and ceiling (dealers sell rips).
Price stays within this channel ~70% of the time in long gamma.
"""

import numpy as np


def extract_channel(
    gex_by_strike: list[dict],
    spot: float,
    floor_threshold: float = 0.15,
    ceiling_threshold: float = 0.15,
    min_width_pct: float = 1.0,
) -> dict:
    if not gex_by_strike or spot <= 0:
        return _empty_channel()

    max_gex = max(abs(s["net_gex"]) for s in gex_by_strike) if gex_by_strike else 0
    if max_gex <= 0:
        return _empty_channel()

    # Floor: highest strike BELOW spot with significant positive GEX
    below_positive = [
        s for s in gex_by_strike
        if s["strike"] < spot and s["net_gex"] > max_gex * floor_threshold
    ]
    floor_strike = max((s["strike"] for s in below_positive), default=None)

    # Ceiling: lowest strike ABOVE spot with significant positive GEX
    above_positive = [
        s for s in gex_by_strike
        if s["strike"] > spot and s["net_gex"] > max_gex * ceiling_threshold
    ]
    ceiling_strike = min((s["strike"] for s in above_positive), default=None)

    # Fallback: use highest absolute GEX
    if floor_strike is None:
        below_any = [s for s in gex_by_strike if s["strike"] < spot and abs(s["net_gex"]) > max_gex * 0.10]
        if below_any:
            floor_strike = max(below_any, key=lambda s: abs(s["net_gex"]))["strike"]

    if ceiling_strike is None:
        above_any = [s for s in gex_by_strike if s["strike"] > spot and abs(s["net_gex"]) > max_gex * 0.10]
        if above_any:
            ceiling_strike = min(above_any, key=lambda s: abs(s["net_gex"]))["strike"]

    # Degenerate channel detection: if the channel is too narrow, widen it
    # by searching for the next significant GEX level further out
    degenerate = False
    if floor_strike and ceiling_strike:
        raw_width = 100 * (ceiling_strike - floor_strike) / spot
        if raw_width < min_width_pct:
            degenerate = True
            floor_strike, ceiling_strike = _widen_channel(
                gex_by_strike, spot, floor_strike, ceiling_strike,
                max_gex, min_width_pct,
            )

    width_pct = None
    channel_position = None
    if floor_strike and ceiling_strike:
        width_pct = round(100 * (ceiling_strike - floor_strike) / spot, 2)
        if ceiling_strike > floor_strike:
            channel_position = round((spot - floor_strike) / (ceiling_strike - floor_strike), 3)

    floor_dist = round(100 * (spot - floor_strike) / spot, 2) if floor_strike else None
    ceiling_dist = round(100 * (ceiling_strike - spot) / spot, 2) if ceiling_strike else None

    floor_gex = 0
    ceiling_gex = 0
    for s in gex_by_strike:
        if floor_strike and s["strike"] == floor_strike:
            floor_gex = s["net_gex"]
        if ceiling_strike and s["strike"] == ceiling_strike:
            ceiling_gex = s["net_gex"]

    return {
        "floor": floor_strike,
        "ceiling": ceiling_strike,
        "floor_distance_pct": floor_dist,
        "ceiling_distance_pct": ceiling_dist,
        "width_pct": width_pct,
        "channel_position": channel_position,
        "floor_gex": round(floor_gex, 2),
        "ceiling_gex": round(ceiling_gex, 2),
        "degenerate": degenerate,
    }


def _widen_channel(gex_by_strike, spot, floor, ceiling, max_gex, min_width_pct):
    """
    When the initial channel is too narrow (e.g. 2 DTE where gamma clusters
    on 1-2 ATM strikes), widen it by finding the next significant GEX
    concentrations further from spot. This gives a usable channel even when
    the primary one is just a pin zone.
    """
    target_half = (min_width_pct / 100) * spot / 2

    # Widen floor downward: find next significant strike below current floor
    candidates_below = sorted(
        [s for s in gex_by_strike
         if s["strike"] < floor and abs(s["net_gex"]) > max_gex * 0.05],
        key=lambda s: s["strike"], reverse=True,
    )
    new_floor = floor
    for s in candidates_below:
        new_floor = s["strike"]
        if spot - new_floor >= target_half:
            break

    # Widen ceiling upward
    candidates_above = sorted(
        [s for s in gex_by_strike
         if s["strike"] > ceiling and abs(s["net_gex"]) > max_gex * 0.05],
        key=lambda s: s["strike"],
    )
    new_ceiling = ceiling
    for s in candidates_above:
        new_ceiling = s["strike"]
        if new_ceiling - spot >= target_half:
            break

    return new_floor, new_ceiling


def _empty_channel():
    return {
        "floor": None, "ceiling": None,
        "floor_distance_pct": None, "ceiling_distance_pct": None,
        "width_pct": None, "channel_position": None,
        "floor_gex": 0, "ceiling_gex": 0,
        "degenerate": False,
    }


def channel_strategy(
    channel: dict,
    regime: str,
    reynolds_regime: str,
    re_gamma: float,
) -> dict:
    """
    Determine the channel-based strategy.
    Returns strategy name, edge type, and guidance notes.
    """
    floor = channel.get("floor")
    ceiling = channel.get("ceiling")
    pos = channel.get("channel_position")
    width = channel.get("width_pct")

    if not floor or not ceiling or not width:
        return {"strategy": "NO_CHANNEL", "edge_type": "NEUTRAL", "notes": [], "channel": channel}

    strategy = "WAIT"
    edge_type = "NEUTRAL"
    notes = []
    degenerate = channel.get("degenerate", False)

    is_long_gamma = regime in ("POSITIVE_GAMMA", "LONG_GAMMA")
    is_laminar = reynolds_regime == "LAMINAR" or re_gamma < 0.7

    if degenerate:
        notes.append("Channel was widened — original bounds were <1% apart (pin zone)")

    if is_long_gamma and is_laminar:
        edge_type = "WITH_DEALER"
        if pos is not None and pos < 0.25:
            strategy = "GEX_FLOOR_BOUNCE"
            notes.append(f"Price near floor — dealers will buy the dip here")
            notes.append(f"BUY CALLS targeting mid-channel")
        elif pos is not None and pos > 0.75:
            strategy = "GEX_CEILING_FADE"
            notes.append(f"Price near ceiling — dealers will sell the rip here")
            notes.append(f"BUY PUTS targeting mid-channel")
        else:
            strategy = "CHANNEL_RANGE"
            notes.append("Mid-channel in long gamma. Fade moves to bounds.")
    elif reynolds_regime == "TURBULENT" or re_gamma > 1.0:
        edge_type = "AGAINST_DEALER"
        strategy = "BREAKOUT_CHANNEL"
        notes.append("Dealers overwhelmed — channel bounds become breakout triggers")
    elif reynolds_regime == "TRANSITIONAL":
        edge_type = "TRANSITIONAL"
        strategy = "TRANSITION_WATCH"
        notes.append("Reynolds at transition zone — watch for regime flip")

    return {
        "strategy": strategy,
        "edge_type": edge_type,
        "notes": notes,
        "channel": channel,
    }
