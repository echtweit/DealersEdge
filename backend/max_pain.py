"""
Max Pain calculator.
Finds the strike price where the total payout to option holders is minimized —
the gravitational center for dealer-driven expiry drift.
"""


def calculate_max_pain(calls: list[dict], puts: list[dict]) -> dict:
    """
    For each candidate strike, sum the intrinsic value of all ITM options.
    The strike with the lowest total payout is max pain.
    """
    strikes = set()
    call_data = []
    put_data = []

    for c in calls:
        k = float(c["strike"])
        oi = int(c.get("openInterest", 0))
        strikes.add(k)
        call_data.append((k, oi))

    for p in puts:
        k = float(p["strike"])
        oi = int(p.get("openInterest", 0))
        strikes.add(k)
        put_data.append((k, oi))

    strikes = sorted(strikes)
    if not strikes:
        return {"max_pain": 0, "pain_by_strike": []}

    pain_by_strike = []

    for test_price in strikes:
        total_pain = 0.0

        for k, oi in call_data:
            if test_price > k:
                total_pain += (test_price - k) * oi * 100

        for k, oi in put_data:
            if test_price < k:
                total_pain += (k - test_price) * oi * 100

        pain_by_strike.append({
            "strike": test_price,
            "total_pain": round(total_pain, 2),
        })

    max_pain_entry = min(pain_by_strike, key=lambda x: x["total_pain"])

    return {
        "max_pain": max_pain_entry["strike"],
        "max_pain_value": max_pain_entry["total_pain"],
        "pain_by_strike": pain_by_strike,
    }


def find_oi_walls(calls: list[dict], puts: list[dict], spot: float) -> dict:
    """
    Find the Call Wall (highest call OI above spot) and
    Put Wall (highest put OI below spot).
    Also returns top 3 walls on each side for nuance.

    To avoid deep-OTM "lottery strike" distortion, prefer walls within a
    proximity band around spot. Fall back to full-chain walls if no strikes
    exist inside the band.
    """
    band_pct = 0.20  # 20% around spot captures relevant dealer hedging zone
    upper = spot * (1 + band_pct)
    lower = spot * (1 - band_pct)

    calls_above = [(float(c["strike"]), int(c.get("openInterest", 0)))
                   for c in calls if float(c["strike"]) > spot]
    puts_below = [(float(p["strike"]), int(p.get("openInterest", 0)))
                  for p in puts if float(p["strike"]) < spot]

    calls_above_near = [(s, oi) for s, oi in calls_above if s <= upper]
    puts_below_near = [(s, oi) for s, oi in puts_below if s >= lower]

    calls_above.sort(key=lambda x: x[1], reverse=True)
    puts_below.sort(key=lambda x: x[1], reverse=True)
    calls_above_near.sort(key=lambda x: x[1], reverse=True)
    puts_below_near.sort(key=lambda x: x[1], reverse=True)

    # Prefer near-spot walls; if empty, fall back to full chain.
    call_pool = calls_above_near if calls_above_near else calls_above
    put_pool = puts_below_near if puts_below_near else puts_below

    call_wall = call_pool[0] if call_pool else (0, 0)
    put_wall = put_pool[0] if put_pool else (0, 0)

    return {
        "call_wall": {"strike": call_wall[0], "oi": call_wall[1]},
        "put_wall": {"strike": put_wall[0], "oi": put_wall[1]},
        "top_call_walls": [{"strike": s, "oi": o} for s, o in call_pool[:5]],
        "top_put_walls": [{"strike": s, "oi": o} for s, o in put_pool[:5]],
        "call_wall_scope": "near_spot" if calls_above_near else "full_chain",
        "put_wall_scope": "near_spot" if puts_below_near else "full_chain",
    }
