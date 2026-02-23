# Implementation Guide: Novel Econophysics Integrations

## Overview

Five research-derived enhancements to the Dealer's Edge tool, ranked by impact and effort.
Each section specifies: the physics concept, the formula, where it integrates into the existing
codebase, the data requirements, and the exact API/frontend surface changes.

---

## 1. Beta-Dependent Gamma Feedback

**Paper:** "Beta-Dependent Gamma Feedback" (arxiv 2511.22766, Nov 2025)

**Core Insight:** Low-beta stocks are disproportionately vulnerable to gamma squeezes. The same
absolute dollar move represents a larger percentage move on a low-beta name, triggering more
aggressive dealer rebalancing. A beta-adjustment makes wall-break probability and Reynolds
number more accurate.

### Formula

```
beta_adj = 1 / max(beta, 0.3)           # amplification factor for low-beta
Re_adjusted = Re_raw * beta_adj          # beta-normalized Reynolds
wall_break_adj = wall_break_raw * beta_adj  # beta-normalized wall-break probability
```

Clamping beta at 0.3 prevents infinite amplification for very-low-beta names.

### Implementation

**File: `backend/technicals.py`**

Add beta computation to `compute_technicals()`:
- After fetching benchmark data in `_compute_relative_strength`, also compute the actual
  beta (covariance of ticker returns vs benchmark returns / variance of benchmark returns)
  over 60-day and 20-day windows.
- Add to the `relative_strength` return dict:
  ```python
  "beta_60d": round(beta_60, 2),
  "beta_20d": round(beta_20, 2),
  "beta_adj_factor": round(1.0 / max(beta_60, 0.3), 2),
  ```

Implementation of beta calculation inside `_compute_relative_strength`:
```python
# After aligning ticker_closes (tc) and bench_closes (bc):
# 60-day beta
if min_len >= 60:
    tc_ret_60 = np.diff(tc[-61:]) / tc[-61:-1]
    bc_ret_60 = np.diff(bc[-61:]) / bc[-61:-1]
    cov = np.cov(tc_ret_60, bc_ret_60)[0, 1]
    var = np.var(bc_ret_60)
    beta_60 = float(cov / var) if var > 1e-10 else 1.0
else:
    beta_60 = 1.0

# 20-day beta (recent, more responsive)
if min_len >= 20:
    tc_ret_20 = np.diff(tc[-21:]) / tc[-21:-1]
    bc_ret_20 = np.diff(bc[-21:]) / bc[-21:-1]
    cov = np.cov(tc_ret_20, bc_ret_20)[0, 1]
    var = np.var(bc_ret_20)
    beta_20 = float(cov / var) if var > 1e-10 else 1.0
else:
    beta_20 = 1.0
```

**File: `backend/gamma_reynolds.py`**

No changes to `compute_gamma_reynolds()` itself — keep it computing the raw Re.
The beta adjustment happens downstream in the consumer (directional_engine).

**File: `backend/directional_engine.py`**

In `classify_thesis()`:
1. Extract beta from technicals:
   ```python
   tech_rs = tech.get("relative_strength", {})
   beta = tech_rs.get("beta_60d", 1.0)
   beta_adj = 1.0 / max(beta, 0.3)
   ```
2. Compute `re_gamma_adj = re_gamma * beta_adj` and use it in the thesis decision tree
   instead of raw `re_gamma` for the threshold checks (>1.0, >0.7, etc.).
3. Pass `beta_adj` to `_estimate_wall_break_probability` and apply as a multiplier on
   the raw probability before clamping.

**File: `backend/straddle_analyzer.py`**

In `_score_regime()`: receive `beta_adj` and boost the Reynolds contribution
by the beta factor. A low-beta stock in turbulent regime gets a higher regime score.

### API Response Changes

Add to the existing `reynolds` response object:
```json
{
  "number": 1.23,
  "number_beta_adj": 1.85,
  "beta_adj_factor": 1.5,
  ...
}
```

Add to `technicals.relative_strength`:
```json
{
  "beta_60d": 0.67,
  "beta_20d": 0.72,
  "beta_adj_factor": 1.49,
  ...
}
```

### Frontend Changes

- **Regime strip:** Show both raw Re and beta-adjusted Re:
  `Re = 1.23 (beta-adj: 1.85)` with the beta-adjusted value highlighted when it
  crosses a threshold that raw Re didn't.
- **Hero tags:** Add a beta tag when beta < 0.7 (low-beta amplifier) or > 1.5 (high-beta damper).
- **Wall-break probability bar:** Use the beta-adjusted probability.

### Difficulty: Low
- 1 new calculation in technicals (beta from existing data)
- Multiplier applied in 2 existing functions
- Minimal frontend — just showing an extra number

---

## 2. Ducournau Collision Probability Refinement

**Paper:** "Stock Market Physical Properties via Stokes' Law" (Ducournau, arxiv 2103.00721)

**Core Insight:** The Reynolds number should incorporate a collision probability — the odds
that price actually *reaches* the wall — not just the balance of speculative vs. dealer gamma.
Ducournau defines this via the odds ratio `P/(1-P)` where P is the collision probability.

### Formula

For each key level (call wall, put wall, channel bounds):
```
distance = |spot - level|
max_move = ATR * sqrt(DTE)           # expected max move envelope
P_collision = min(1.0, max_move / (2 * distance))   # simple probability proxy
odds_ratio = P / (1 - P)             # Ducournau's odds ratio
```

Then refine wall-break probability:
```
Re_collision = Re_raw * sqrt(odds_ratio)
wall_break_refined = base_prob * (1 + log(1 + odds_ratio)) / 2
```

The `sqrt` dampens the effect so it's a refinement, not a replacement.

### Implementation

**File: `backend/directional_engine.py`**

New function `_collision_probability(spot, level, atr_dollar, dte)`:
```python
def _collision_probability(spot, level, atr_dollar, dte):
    """
    Ducournau-inspired collision probability: what are the odds that
    price reaches 'level' within DTE days, given the ATR-derived
    expected move envelope?
    """
    if atr_dollar <= 0 or dte <= 0 or level <= 0:
        return 0.5  # neutral prior
    distance = abs(spot - level)
    max_move = atr_dollar * np.sqrt(dte)
    p = min(0.95, max(0.05, max_move / (2 * distance))) if distance > 0 else 0.95
    return p
```

Modify `_estimate_wall_break_probability` to accept `atr_dollar`, `dte`,
`call_wall_strike`, and `put_wall_strike`:
1. Compute collision probability to the nearest wall.
2. Compute odds ratio: `odds = p / (1 - p)`
3. Apply as an adjustment: `prob *= (1 + np.log1p(odds)) / 2`
4. Re-clamp to [5, 95].

This replaces the current heuristic adjustment (+5 for Re > 0.3) with a physics-grounded one.

**File: `backend/directional_engine.py` — `_build_level_actions`**

For each level action entry, compute and include the collision probability:
```python
p_collision = _collision_probability(spot, level, atr_dollar, dte)
# Add to each action dict:
"collision_prob": round(p_collision * 100, 1),
"collision_label": "LIKELY" if p_collision > 0.6 else "POSSIBLE" if p_collision > 0.3 else "UNLIKELY",
```

This tells the trader "how likely is price to even reach this level within DTE?"

**File: `backend/directional_engine.py` — `classify_thesis`**

Pass `atr_dollar` and `dte` into `_estimate_wall_break_probability` (they're
already available in scope from the technicals extraction).

### API Response Changes

Wall-break probability gains collision context:
```json
{
  "probability": 42,
  "collision_prob_call_wall": 65.2,
  "collision_prob_put_wall": 33.8,
  ...
}
```

Each level action gains:
```json
{
  "level": 605,
  "collision_prob": 72.3,
  "collision_label": "LIKELY",
  ...
}
```

### Frontend Changes

- **Level action cards:** Show collision probability as a small badge:
  `72% reach probability` or `UNLIKELY to reach (23%)`.
- **Wall-break section:** Show the split — "65% chance of reaching the wall,
  42% chance of breaking through" — giving a more nuanced picture.

### Difficulty: Low
- 1 new utility function (~15 lines)
- Modification of 1 existing function (wall-break probability)
- Enhancement of existing level action entries
- Small frontend additions (badges)

---

## 3. Kanazawa Collision Time (Expected Time to Level)

**Paper:** "Exact Solution to Two-Body Dealer Model" (Kanazawa et al., arxiv 2205.15558)

**Core Insight:** Mean time for a random-walk price to reach a barrier at distance L with
volatility σ is `T = L² / (2σ²)`. This gives a physics-derived ETA for when price should
reach each key level, replacing our heuristic DTE guidance.

### Formula

```
distance = |spot - level|            # dollars
sigma = ATR_daily                    # daily dollar volatility
T_expected = distance² / (2 * sigma²)  # expected days to reach
```

With regime adjustment:
```
if regime == TURBULENT:
    T_adjusted = T_expected * 0.6    # moves happen faster in turbulent
elif regime == LONG_GAMMA:
    T_adjusted = T_expected * 1.4    # moves dampened, slower
else:
    T_adjusted = T_expected
```

### Implementation

**New file: `backend/collision_time.py`**

```python
"""
Collision Time Calculator — Kanazawa kinetic theory adaptation.
Estimates expected time for price to reach key dealer levels using
the first-passage-time formula from the two-body dealer model.

T_expected = L² / (2σ²)
where L = distance to level, σ = daily ATR (dollar volatility)
"""

import numpy as np


def compute_collision_times(
    spot: float,
    levels: dict,           # {"call_wall": 610, "put_wall": 580, ...}
    atr_dollar: float,
    acf_regime: str,
    reynolds_regime: str,
    dte: int,
) -> list[dict]:
    """
    For each key level, compute the expected number of trading days
    for price to reach it, using first-passage-time theory.
    """
    if atr_dollar <= 0:
        return []

    sigma = atr_dollar
    sigma_sq = sigma ** 2

    # Regime multiplier: turbulent = faster arrival, dampened = slower
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

        distance = abs(spot - price)
        if distance < 0.01:
            t_raw = 0.0
        else:
            t_raw = (distance ** 2) / (2 * sigma_sq)

        t_adjusted = t_raw * regime_mult

        # Probability of reaching within DTE
        # Using cumulative first-passage distribution:
        # P(T <= dte) = erfc(distance / (sigma * sqrt(2 * dte)))
        if dte > 0 and sigma > 0:
            z = distance / (sigma * np.sqrt(2 * dte))
            p_within_dte = float(1.0 - _erfc_approx(z))
        else:
            p_within_dte = 0.0

        urgency = "NOW" if t_adjusted < 1 else \
                  "IMMINENT" if t_adjusted < 2 else \
                  "SOON" if t_adjusted <= dte else \
                  "UNLIKELY" if t_adjusted > dte * 2 else "POSSIBLE"

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


def _erfc_approx(x):
    """Complementary error function approximation (Abramowitz & Stegun)."""
    from scipy.special import erfc
    return float(erfc(x))
```

**File: `backend/main.py`**

After computing key_levels and technicals, call `compute_collision_times`:
```python
from collision_time import compute_collision_times

# After step 10 (key_levels):
atr_dollar = technicals.get("atr", {}).get("atr", 0)
collision_levels = {
    "call_wall": walls["call_wall"]["strike"],
    "put_wall": walls["put_wall"]["strike"],
    "max_pain": pain["max_pain"],
    "flip_point": gex["flip_point"],
    "abs_gamma_strike": gex["abs_gamma_strike"],
    "channel_floor": channel.get("floor"),
    "channel_ceiling": channel.get("ceiling"),
}
collision_times = compute_collision_times(
    spot, collision_levels, atr_dollar,
    acf.get("regime", "NEUTRAL"), reynolds["regime"], dte,
)

# Add to response:
"collision_times": collision_times,
```

**File: `backend/directional_engine.py` — `_build_level_actions`**

Accept `collision_times` and enrich each level action with the expected arrival time:
```python
"expected_days": ct["expected_days_adj"],
"prob_within_dte": ct["prob_within_dte"],
"urgency": ct["urgency"],
```

This replaces vague distance_pct with a concrete "price should reach this level in ~X days".

### API Response Changes

New top-level key:
```json
{
  "collision_times": [
    {
      "level_label": "abs_gamma_strike",
      "level_price": 595.0,
      "distance": 3.50,
      "expected_days_raw": 0.8,
      "expected_days_adj": 0.5,
      "prob_within_dte": 92.3,
      "urgency": "NOW"
    },
    ...
  ]
}
```

### Frontend Changes

- **Level action cards:** Replace or supplement the "X% away" badge with
  "~2.3 days to reach | 78% within DTE" — far more actionable than raw distance.
- **New "Timeline" mini-section** in the hero area or above level actions:
  A horizontal timeline showing expected arrival at each level, color-coded
  by urgency (NOW=green, IMMINENT=yellow, UNLIKELY=red).
- **Position cards:** DTE guidance can now be physics-informed:
  "Buy 7 DTE — expected to reach call wall in 2.3 days (78% probability)"

### Difficulty: Medium
- 1 new file (~80 lines)
- Integration into main.py (add call + response field)
- Enhancement of level actions with arrival time data
- Frontend timeline visualization (new visual element)

---

## 4. GEX Entropy — Phase Transition Early Warning

**Paper:** "Self-Organization to the Edge of Phase Transition" (Frontiers in Physics, 2024)

**Core Insight:** Markets self-organize to the edge of criticality. The entropy of the GEX
distribution across strikes measures how concentrated vs. dispersed dealer positioning is.
Low entropy = gamma concentrated at few strikes = system near a phase transition = unstable.
High entropy = gamma spread out = stable equilibrium.

### Formula

Shannon entropy of the GEX profile:
```
p_i = |net_gex_i| / Σ|net_gex_j|     # normalized probability distribution
H = -Σ p_i * log(p_i)                 # Shannon entropy (in nats)

# Normalize to [0, 1]:
H_max = log(N)                         # max entropy for N strikes
H_normalized = H / H_max
```

Interpretation:
```
H_norm < 0.3  → CRITICAL     — gamma clustered, phase transition imminent
H_norm < 0.5  → APPROACHING  — significant clustering, elevated risk
H_norm < 0.7  → MODERATE     — some clustering but stable
H_norm >= 0.7 → DISPERSED    — evenly spread, stable regime
```

### Implementation

**File: `backend/gex_calculator.py`**

New function `compute_gex_entropy(gex_by_strike)`:
```python
def compute_gex_entropy(gex_by_strike: list[dict], spot: float, atm_range_pct: float = 10.0) -> dict:
    """
    Compute Shannon entropy of the GEX distribution to measure how
    concentrated dealer gamma is across strikes. Low entropy = unstable,
    concentrated gamma → approaching phase transition.

    Only considers strikes within atm_range_pct of spot to avoid noise
    from far OTM strikes with near-zero GEX.
    """
    if not gex_by_strike:
        return {"entropy": 1.0, "entropy_norm": 1.0, "regime": "DISPERSED", "n_strikes": 0}

    # Filter to relevant strikes (within range of spot)
    relevant = [
        s for s in gex_by_strike
        if abs(s["strike"] - spot) / spot * 100 <= atm_range_pct
        and abs(s["net_gex"]) > 0
    ]

    if len(relevant) < 3:
        return {"entropy": 1.0, "entropy_norm": 1.0, "regime": "DISPERSED", "n_strikes": len(relevant)}

    gex_abs = np.array([abs(s["net_gex"]) for s in relevant])
    total = gex_abs.sum()
    if total <= 0:
        return {"entropy": 1.0, "entropy_norm": 1.0, "regime": "DISPERSED", "n_strikes": len(relevant)}

    probs = gex_abs / total
    # Remove zero probabilities to avoid log(0)
    probs = probs[probs > 0]

    H = float(-np.sum(probs * np.log(probs)))
    H_max = np.log(len(probs))
    H_norm = float(H / H_max) if H_max > 0 else 1.0

    # Identify the dominant strike(s) — where is gamma most concentrated?
    top_idx = np.argsort(gex_abs)[-3:]  # top 3 concentrations
    top_strikes = [{"strike": relevant[i]["strike"], "gex_share_pct": round(probs[i] * 100, 1)}
                   for i in top_idx if i < len(relevant)]
    top_strikes.sort(key=lambda s: s["gex_share_pct"], reverse=True)

    if H_norm < 0.3:
        regime = "CRITICAL"
        desc = f"Gamma highly concentrated at {top_strikes[0]['strike']:.0f} ({top_strikes[0]['gex_share_pct']:.0f}% of total) — phase transition risk"
    elif H_norm < 0.5:
        regime = "APPROACHING"
        desc = "Significant gamma clustering — elevated instability risk"
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
        "top_concentrations": top_strikes,
    }
```

Call this from `calculate_gex_profile` after building `gex_by_strike`:
```python
entropy = compute_gex_entropy(gex_by_strike, spot)
# Add to return dict:
"entropy": entropy,
```

**File: `backend/main.py`**

Pass the entropy data through to the API response. Add it under the
existing `gex_profile` section:
```python
"gex_profile": {
    ...existing fields...
    "entropy": gex["entropy"],
},
```

**File: `backend/directional_engine.py`**

Use entropy as an input to thesis classification:
- If `entropy_regime == "CRITICAL"` and Reynolds is TRANSITIONAL, upgrade
  to treat as TURBULENT (the system is primed for a phase transition).
- If `entropy_regime == "CRITICAL"`, boost wall-break probability by +10.
- Add entropy warning to the avoid list when regime is CRITICAL.

**File: `backend/straddle_analyzer.py`**

Add entropy to `_score_catalyst`:
- CRITICAL entropy: +8 (high probability of big move)
- APPROACHING entropy: +4
- This directly improves straddle timing

### API Response Changes

Under `gex_profile`:
```json
{
  "entropy": {
    "entropy": 1.832,
    "entropy_norm": 0.45,
    "regime": "APPROACHING",
    "description": "Significant gamma clustering — elevated instability risk",
    "n_strikes": 28,
    "top_concentrations": [
      {"strike": 600, "gex_share_pct": 22.1},
      {"strike": 605, "gex_share_pct": 15.8},
      {"strike": 595, "gex_share_pct": 12.3}
    ]
  }
}
```

### Frontend Changes

- **Regime strip:** New "GEX Entropy" chip alongside the existing regime chips.
  Color-coded: CRITICAL=red, APPROACHING=yellow, MODERATE=neutral, DISPERSED=green.
  Shows: `Entropy: 0.45 (APPROACHING) — gamma clustering at $600`
- **GEX chart:** Add a visual indicator showing the concentration — maybe thicker
  bars or a highlight on the dominant strike(s).
- **Phase section:** Integrate entropy warning with the existing phase transition
  warning for a combined early-warning system.

### Difficulty: Medium
- 1 new function in gex_calculator (~50 lines)
- Integration into 3 existing modules (small additions each)
- New regime chip on frontend
- Optional GEX chart enhancement

---

## 5. Hawkes Self-Excitation Index

**Paper:** "Unified Theory of Order Flow, Market Impact, and Volatility" (arxiv 2601.23172)

**Core Insight:** Hawkes processes model self-exciting feedback loops: an event increases the
probability of future events. In markets, this manifests as moves begetting more moves — exactly
what happens when dealers are short gamma and hedging amplifies price action.

A practical self-excitation metric from intraday returns would complement (not replace) the
ACF regime detection. Where ACF measures autocorrelation at fixed lags, self-excitation
measures whether clustered moves *accelerate*.

### Formula

Simplified self-excitation index from intraday returns:
```
For each bar i:
  If |return_i| > threshold (e.g., 0.1%) AND sign matches previous qualifying move:
    cluster_count += 1
    intensity_sum += |return_i|
  Else:
    Record cluster, start new one

Self-Excitation Index (SEI) = Σ(cluster_size * cluster_intensity) / N_clusters
```

Higher SEI → more self-exciting behavior → stronger dealer amplification.

Calibrate thresholds:
```
SEI > 2.0  → HIGH_EXCITATION     — strong feedback loops active
SEI > 1.0  → MODERATE_EXCITATION — some clustering present
SEI > 0.5  → LOW_EXCITATION      — occasional bursts
SEI <= 0.5 → NONE                — no meaningful self-excitation
```

### Implementation

**File: `backend/acf_engine.py`**

New function `compute_self_excitation(prices, threshold_pct=0.1)`:
```python
def compute_self_excitation(prices: np.ndarray, threshold_pct: float = 0.1) -> dict:
    """
    Compute a simplified Hawkes self-excitation index from intraday prices.
    Measures how often and how intensely moves cluster in the same direction —
    the hallmark of dealer amplification in negative gamma.

    A move is "self-exciting" if it:
    1. Exceeds the threshold
    2. Has the same sign as the previous qualifying move
    3. Occurs within a short time window (consecutive or near-consecutive bars)

    Returns a self-excitation index (SEI) and cluster statistics.
    """
    returns = pd.Series(prices).pct_change().dropna().values
    if len(returns) < 20:
        return {"sei": 0, "regime": "NONE", "n_clusters": 0, "avg_cluster_size": 0}

    threshold = threshold_pct / 100
    clusters = []
    current_cluster = []
    last_sign = 0

    for r in returns:
        if abs(r) >= threshold:
            sign = 1 if r > 0 else -1
            if sign == last_sign or last_sign == 0:
                current_cluster.append(abs(r))
                last_sign = sign
            else:
                if len(current_cluster) >= 2:
                    clusters.append(current_cluster[:])
                current_cluster = [abs(r)]
                last_sign = sign
        else:
            if len(current_cluster) >= 2:
                clusters.append(current_cluster[:])
            current_cluster = []
            last_sign = 0

    if len(current_cluster) >= 2:
        clusters.append(current_cluster)

    if not clusters:
        return {"sei": 0, "regime": "NONE", "n_clusters": 0, "avg_cluster_size": 0,
                "max_cluster_size": 0, "total_excitation_events": 0}

    cluster_scores = [len(c) * sum(c) * 10000 for c in clusters]  # scale up for readability
    sei = float(np.mean(cluster_scores))
    max_cluster = max(len(c) for c in clusters)
    avg_size = float(np.mean([len(c) for c in clusters]))

    if sei > 2.0:
        regime = "HIGH_EXCITATION"
        desc = "Strong self-exciting feedback — moves amplify rapidly"
    elif sei > 1.0:
        regime = "MODERATE_EXCITATION"
        desc = "Some self-exciting behavior — occasional momentum bursts"
    elif sei > 0.5:
        regime = "LOW_EXCITATION"
        desc = "Weak self-excitation — moves don't consistently amplify"
    else:
        regime = "NONE"
        desc = "No meaningful self-excitation — mean-reversion dominant"

    return {
        "sei": round(sei, 3),
        "regime": regime,
        "description": desc,
        "n_clusters": len(clusters),
        "avg_cluster_size": round(avg_size, 1),
        "max_cluster_size": max_cluster,
        "total_excitation_events": sum(len(c) for c in clusters),
    }
```

Integrate into `scan_ticker_acf`:
```python
# After computing daily ACF results, also compute self-excitation:
all_prices = df["Close"].values
self_excitation = compute_self_excitation(all_prices)

# Add to return dict:
"self_excitation": self_excitation,
```

**File: `backend/directional_engine.py`**

Add self-excitation as a signal:
- In the thesis decision tree, HIGH_EXCITATION can serve as ACF confirmation:
  ```python
  sei = acf.get("self_excitation", {}).get("sei", 0)
  sei_regime = acf.get("self_excitation", {}).get("regime", "NONE")
  # If ACF is amplified AND self-excitation confirms:
  acf_net_momentum = (pct_amp > pct_damp and (pct_amp > 13 or acf1 > 0.05)) or \
                     (sei_regime in ("HIGH_EXCITATION", "MODERATE_EXCITATION") and acf1 > 0)
  ```
- In wall-break probability, HIGH_EXCITATION adds +8, MODERATE adds +4.

**File: `backend/straddle_analyzer.py`**

In `_score_catalyst`, add self-excitation scoring:
```python
sei = acf.get("self_excitation", {}).get("sei", 0)
if sei > 2.0:
    score += 6
elif sei > 1.0:
    score += 3
```

### API Response Changes

Under `acf_data`:
```json
{
  "self_excitation": {
    "sei": 1.85,
    "regime": "MODERATE_EXCITATION",
    "description": "Some self-exciting behavior — occasional momentum bursts",
    "n_clusters": 7,
    "avg_cluster_size": 3.2,
    "max_cluster_size": 6
  }
}
```

### Frontend Changes

- **Regime strip:** New "Self-Excitation" chip or integrate into existing ACF chip:
  `ACF: LONG_GAMMA (-0.12) | SEI: 1.85 (MODERATE)`
- **Straddle reasoning:** Include SEI in the reasoning text when it's elevated.

### Difficulty: Medium-High
- 1 new function in acf_engine (~60 lines)
- Integration into 3 existing modules
- New data in API response
- Requires careful threshold calibration (the threshold_pct and SEI thresholds
  should be tested against historical data for SPY, TSLA, etc.)

---

## Implementation Order

```
Phase 1 (Quick wins — do together):
  1. Beta-Dependent Gamma Feedback
  2. Collision Probability Refinement
  → These refine existing calculations with minimal new code

Phase 2 (New capabilities):
  3. Collision Time (Expected Time to Level)
  4. GEX Entropy (Phase Transition Early Warning)
  → These add genuinely new analytical dimensions

Phase 3 (Advanced):
  5. Hawkes Self-Excitation Index
  → This requires the most calibration and testing
```

## Testing Strategy

For each enhancement:

1. **Unit test with known data:** Create synthetic options chains and price histories
   with known properties (e.g., concentrated GEX for entropy, clustered returns for SEI).
2. **Regression test:** Run the scanner against SPY, TSLA, META, AMZN, MRNA and compare
   outputs before/after to ensure no thesis classification regressions.
3. **Backtest sanity check:** Verify that the new signals would have provided useful
   information on known historical events (e.g., Jan 2021 GME squeeze should show
   HIGH_EXCITATION + CRITICAL entropy + high beta-adjusted Re).

## Files Modified Summary

| File | Phase 1 | Phase 2 | Phase 3 |
|------|---------|---------|---------|
| `backend/technicals.py` | Beta calc | — | — |
| `backend/gamma_reynolds.py` | — | — | — |
| `backend/directional_engine.py` | Beta-adj Re + Collision prob | Entropy input | SEI input |
| `backend/gex_calculator.py` | — | Entropy calc | — |
| `backend/collision_time.py` | — | NEW FILE | — |
| `backend/acf_engine.py` | — | — | SEI calc |
| `backend/straddle_analyzer.py` | Beta-adj score | Entropy score | SEI score |
| `backend/main.py` | Response fields | Collision times call | SEI response |
| `frontend/src/main.js` | Beta/collision UI | Timeline + entropy chip | SEI chip |
| `frontend/src/style.css` | Minor additions | Timeline styles | Minor additions |

## Data Requirements

All enhancements use **existing data sources** (yfinance). No new APIs needed.

| Enhancement | Data Source | Already Fetched? |
|-------------|-----------|-----------------|
| Beta | Daily closes (ticker + SPY) | Yes (in `_compute_relative_strength`) |
| Collision Probability | ATR + spot + levels | Yes |
| Collision Time | ATR + spot + levels | Yes |
| GEX Entropy | GEX profile by strike | Yes |
| Self-Excitation | Intraday 2m prices | Yes (in `scan_ticker_acf`) |
