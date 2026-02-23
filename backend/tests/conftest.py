"""
Shared test fixtures — synthetic, deterministic data for all test modules.
No network calls, no yfinance dependency.
"""

import numpy as np
import pytest


# ── Option Chain Generators ──────────────────────────────────────────────────


def make_option(strike, oi=100, volume=50, iv=0.30, bid=1.0, ask=1.2, last=1.1):
    return {
        "strike": strike,
        "openInterest": oi,
        "volume": volume,
        "impliedVolatility": iv,
        "bid": bid,
        "ask": ask,
        "lastPrice": last,
    }


def make_chain(spot, n_strikes=21, base_iv=0.30, oi_peak_offset=0, spread=1.0):
    """
    Generate a symmetric chain centered on spot.
    oi_peak_offset: shift OI peak above (+) or below (-) spot.
    """
    half = n_strikes // 2
    if spot > 500:
        inc = 5.0
    elif spot > 100:
        inc = 5.0
    elif spot > 50:
        inc = 2.5
    else:
        inc = 1.0

    center = round(spot / inc) * inc
    strikes = [center + (i - half) * inc * spread for i in range(n_strikes)]

    calls, puts = [], []
    for k in strikes:
        moneyness = abs(k - spot) / spot
        iv_smile = base_iv * (1 + 0.5 * moneyness)
        oi_base = max(10, int(500 * np.exp(-3 * moneyness)))

        if oi_peak_offset != 0:
            peak_strike = spot + oi_peak_offset
            oi_base = max(10, int(800 * np.exp(-2 * abs(k - peak_strike) / spot)))

        call_intrinsic = max(0, spot - k)
        put_intrinsic = max(0, k - spot)
        call_mid = max(0.05, call_intrinsic + base_iv * spot * 0.05)
        put_mid = max(0.05, put_intrinsic + base_iv * spot * 0.05)

        calls.append(make_option(
            strike=k, oi=oi_base, volume=oi_base // 3,
            iv=iv_smile, bid=call_mid * 0.95, ask=call_mid * 1.05, last=call_mid,
        ))
        puts.append(make_option(
            strike=k, oi=oi_base, volume=oi_base // 3,
            iv=iv_smile, bid=put_mid * 0.95, ask=put_mid * 1.05, last=put_mid,
        ))

    return calls, puts


def make_price_history(spot=100, n_days=250, daily_vol=0.015, trend=0.0, seed=42):
    """Generate deterministic OHLCV bars."""
    rng = np.random.RandomState(seed)
    prices = [spot]
    for _ in range(n_days - 1):
        ret = trend + daily_vol * rng.randn()
        prices.append(prices[-1] * (1 + ret))

    bars = []
    for i, close in enumerate(prices):
        noise = daily_vol * spot * 0.5
        high = close + abs(rng.randn()) * noise
        low = close - abs(rng.randn()) * noise
        opn = close + rng.randn() * noise * 0.3
        vol = int(1e6 + rng.randint(-2e5, 2e5))
        bars.append({
            "date": f"2025-{1 + i // 30:02d}-{1 + i % 28:02d}",
            "open": round(float(opn), 2),
            "high": round(float(max(high, opn, close)), 2),
            "low": round(float(min(low, opn, close)), 2),
            "close": round(float(close), 2),
            "volume": vol,
        })
    return bars


# ── Pre-built Regime Dicts ───────────────────────────────────────────────────


def make_acf(regime="LONG_GAMMA", acf1=-0.15, pct_amp=5, pct_damp=90, sei=30):
    return {
        "symbol": "TEST",
        "status": "OK",
        "n_days": 5,
        "mean_acf1": acf1,
        "regime": regime,
        "pct_dampened": pct_damp,
        "pct_amplified": pct_amp,
        "acf_trend": "STABLE",
        "acf_slope": 0.0,
        "stability": "STABLE",
        "transitions_per_day": 0.1,
        "at_squeeze_ceiling": False,
        "daily_results": [
            {"date": f"2025-01-0{i+1}", "lag1_acf": acf1, "regime": regime, "n_bars": 100}
            for i in range(5)
        ],
        "self_excitation": {
            "sei": sei, "regime": "NONE" if sei < 40 else "MODERATE_EXCITATION",
            "description": "", "n_clusters": 0, "avg_cluster_size": 0,
            "max_cluster_size": 0, "total_excitation_events": 0,
        },
    }


def make_reynolds(regime="LAMINAR", re_number=0.4, cp_ratio=1.2, atm_iv=0.25):
    return {
        "reynolds_number": re_number,
        "speculative_gamma": 1000,
        "dealer_gamma": 2500,
        "regime": regime,
        "call_put_ratio": cp_ratio,
        "call_volume": 5000,
        "put_volume": 4000,
        "call_oi": 50000,
        "put_oi": 40000,
        "atm_iv": atm_iv,
    }


def make_phase(regime="LAMINAR", pct_amplified=5, distance=8):
    return {
        "pct_amplified": pct_amplified,
        "distance_to_transition": distance,
        "regime": regime,
        "warning": None,
        "n_amplified_days": 1,
        "window": 5,
    }


def make_channel(floor=95, ceiling=105, spot=100, degenerate=False):
    width_pct = round((ceiling - floor) / spot * 100, 2) if floor and ceiling else None
    pos = round((spot - floor) / (ceiling - floor), 3) if floor and ceiling and ceiling > floor else None
    return {
        "floor": floor,
        "ceiling": ceiling,
        "floor_distance_pct": round((spot - floor) / spot * 100, 2) if floor else None,
        "ceiling_distance_pct": round((ceiling - spot) / spot * 100, 2) if ceiling else None,
        "width_pct": width_pct,
        "channel_position": pos,
        "floor_gex": 500,
        "ceiling_gex": 400,
        "degenerate": degenerate,
    }


def make_channel_strat(strategy="CHANNEL_RANGE", edge="WITH_DEALER"):
    return {
        "strategy": strategy,
        "edge_type": edge,
        "notes": [],
        "channel": make_channel(),
    }


def make_technicals(
    trend="UPTREND", atr=2.0, atr_pct=2.0, alignment=2,
    rs_label="OUTPERFORMING", beta=1.0, spot=100,
):
    return {
        "moving_averages": {
            "sma_20": {"value": spot * 0.98, "distance_pct": 2.0, "position": "ABOVE", "slope": "RISING", "slope_pct": 0.5},
            "sma_50": {"value": spot * 0.95, "distance_pct": 5.0, "position": "ABOVE", "slope": "RISING", "slope_pct": 0.3},
            "sma_200": {"value": spot * 0.90, "distance_pct": 10.0, "position": "ABOVE", "slope": "RISING", "slope_pct": 0.2},
            "alignment": alignment,
            "alignment_label": "BULL" if alignment >= 2 else "BEAR" if alignment <= -2 else "NEUTRAL",
            "alignment_desc": "",
            "alignment_details": [],
            "cross": None,
        },
        "atr": {
            "atr": atr,
            "atr_pct": atr_pct,
            "period": 14,
            "recent_range": atr * 0.9,
            "recent_range_pct": atr_pct * 0.9,
            "atr_trend": "STABLE",
            "atr_change_pct": 0,
        },
        "relative_strength": {
            "benchmark": "SPY",
            "rs_5d": 1.5, "rs_20d": 1.0, "rs_60d": 0.5,
            "rs_trend": "STABLE",
            "rs_label": rs_label,
            "rs_desc": "",
            "beta_60d": beta,
            "beta_20d": beta,
            "beta_adj_factor": round(1.0 / max(beta, 0.3), 2),
        },
        "vwap": {
            "vwap_5d": {"value": spot * 0.99, "upper_1": spot * 1.01, "lower_1": spot * 0.97, "upper_2": spot * 1.03, "lower_2": spot * 0.95, "std": spot * 0.02, "distance_pct": 1.0, "position": "ABOVE"},
            "vwap_20d": {"value": spot * 0.98, "upper_1": spot * 1.02, "lower_1": spot * 0.94, "upper_2": spot * 1.06, "lower_2": spot * 0.90, "std": spot * 0.04, "distance_pct": 2.0, "position": "ABOVE"},
            "anchored_monthly": spot * 0.985,
            "context": "ABOVE",
            "context_desc": "",
        },
        "trend": {
            "trend_label": trend,
            "trend_desc": "",
            "trend_score": 1 if "UP" in trend else -1 if "DOWN" in trend else 0,
            "vol_label": "NORMAL_VOL",
            "vol_desc": "",
            "tech_bias": "LEAN_BULLISH" if "UP" in trend else "LEAN_BEARISH" if "DOWN" in trend else "NEUTRAL",
        },
    }


# ── Pytest Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def spot():
    return 100.0


@pytest.fixture
def calls_puts(spot):
    return make_chain(spot)


@pytest.fixture
def calls(calls_puts):
    return calls_puts[0]


@pytest.fixture
def puts(calls_puts):
    return calls_puts[1]


@pytest.fixture
def price_history():
    return make_price_history(spot=100, n_days=250)


@pytest.fixture
def short_price_history():
    return make_price_history(spot=100, n_days=10)


@pytest.fixture
def acf_long_gamma():
    return make_acf("LONG_GAMMA", acf1=-0.15, pct_amp=5, pct_damp=90)


@pytest.fixture
def acf_short_gamma():
    return make_acf("SHORT_GAMMA", acf1=0.12, pct_amp=60, pct_damp=30, sei=120)


@pytest.fixture
def acf_neutral():
    return make_acf("NEUTRAL", acf1=0.0, pct_amp=50, pct_damp=50)


@pytest.fixture
def reynolds_laminar():
    return make_reynolds("LAMINAR", 0.4)


@pytest.fixture
def reynolds_turbulent():
    return make_reynolds("TURBULENT", 1.5)


@pytest.fixture
def phase_laminar():
    return make_phase("LAMINAR", pct_amplified=5)


@pytest.fixture
def phase_turbulent():
    return make_phase("TURBULENT", pct_amplified=20)


@pytest.fixture
def channel_standard(spot):
    return make_channel(spot * 0.95, spot * 1.05, spot)


@pytest.fixture
def channel_strat_range():
    return make_channel_strat("CHANNEL_RANGE", "WITH_DEALER")


@pytest.fixture
def technicals_bull(spot):
    return make_technicals("UPTREND", atr=2.0, atr_pct=2.0, alignment=2, spot=spot)


@pytest.fixture
def technicals_bear(spot):
    return make_technicals("DOWNTREND", atr=2.0, atr_pct=2.0, alignment=-2, rs_label="UNDERPERFORMING", spot=spot)
