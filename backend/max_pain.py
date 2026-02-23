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
    """
    calls_above = [(float(c["strike"]), int(c.get("openInterest", 0)))
                   for c in calls if float(c["strike"]) > spot]
    puts_below = [(float(p["strike"]), int(p.get("openInterest", 0)))
                  for p in puts if float(p["strike"]) < spot]

    calls_above.sort(key=lambda x: x[1], reverse=True)
    puts_below.sort(key=lambda x: x[1], reverse=True)

    call_wall = calls_above[0] if calls_above else (0, 0)
    put_wall = puts_below[0] if puts_below else (0, 0)

    return {
        "call_wall": {"strike": call_wall[0], "oi": call_wall[1]},
        "put_wall": {"strike": put_wall[0], "oi": put_wall[1]},
        "top_call_walls": [{"strike": s, "oi": o} for s, o in calls_above[:5]],
        "top_put_walls": [{"strike": s, "oi": o} for s, o in puts_below[:5]],
    }
