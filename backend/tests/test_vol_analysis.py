"""Tests for vol_analysis — IV/HV, skew, term structure, VRP, vol edge."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest
from vol_analysis import (
    compute_vol_analysis,
    _compute_iv_vs_hv,
    _compute_skew,
    _compute_term_structure,
    _compute_vrp,
    _compute_vol_edge,
)
from tests.conftest import make_chain, make_price_history


class TestComputeIvVsHv:
    def test_cheap_iv(self):
        """When IV < HV, context should be CHEAP or SLIGHT_DISCOUNT."""
        closes = _make_multiplicative_prices(100, 100, daily_vol=0.02, seed=42)
        result = _compute_iv_vs_hv(0.10, closes, 10)  # 10% IV vs ~32% HV
        assert result["iv_hv_ratio"] < 1.0
        assert result["context"] in ("CHEAP", "SLIGHT_DISCOUNT")

    def test_expensive_iv(self):
        """When IV >> HV, context should be EXPENSIVE."""
        closes = _make_multiplicative_prices(100, 100, daily_vol=0.005, seed=42)
        result = _compute_iv_vs_hv(0.80, closes, 10)  # 80% IV vs ~8% HV
        assert result["context"] in ("EXPENSIVE", "VERY_EXPENSIVE")
        assert result["iv_hv_ratio"] > 1.3

    def test_fair_iv(self):
        """When IV ≈ HV, context should be FAIR."""
        closes = _make_multiplicative_prices(100, 100, daily_vol=0.015, seed=42)
        hv = float(np.std(np.diff(np.log(closes[-20:]))) * np.sqrt(252))
        result = _compute_iv_vs_hv(hv, closes, 20)
        assert result["context"] in ("FAIR", "SLIGHT_DISCOUNT", "SLIGHT_PREMIUM")

    def test_insufficient_data(self):
        result = _compute_iv_vs_hv(0.30, [100] * 10, 10)
        assert result["label"] == "Insufficient data"
        assert result["hv_10d"] == 0

    def test_zero_iv_gets_fallback(self):
        closes = _make_multiplicative_prices(100, 100, daily_vol=0.01, seed=42)
        result = _compute_iv_vs_hv(0, closes, 10)
        assert result["atm_iv"] == 30.0  # 0.3 * 100

    def test_hv_windows_populated(self):
        closes = _make_multiplicative_prices(100, 100, daily_vol=0.01, seed=42)
        result = _compute_iv_vs_hv(0.25, closes, 20)
        assert result["hv_10d"] > 0
        assert result["hv_20d"] > 0

    def test_ratio_always_positive(self):
        closes = _make_multiplicative_prices(100, 100, daily_vol=0.01, seed=42)
        result = _compute_iv_vs_hv(0.05, closes, 10)
        assert result["iv_hv_ratio"] > 0


class TestComputeSkew:
    def test_normal_skew(self):
        calls, puts = make_chain(100, n_strikes=21, base_iv=0.25)
        result = _compute_skew(calls, puts, 100, 10)
        assert "regime" in result
        assert result["regime"] != "UNKNOWN"

    def test_empty_chains(self):
        result = _compute_skew([], [], 100, 10)
        assert result["regime"] == "UNKNOWN"

    def test_puts_only(self):
        _, puts = make_chain(100)
        result = _compute_skew([], puts, 100, 10)
        assert result["regime"] == "UNKNOWN"


class TestComputeTermStructure:
    def test_contango(self):
        """Longer-dated IV > shorter → contango."""
        chains = [
            {"dte": 5, "expiration": "2025-01-05",
             "calls": [make_option_iv(100, 0.20)], "puts": [make_option_iv(100, 0.20)]},
            {"dte": 30, "expiration": "2025-01-30",
             "calls": [make_option_iv(100, 0.30)], "puts": [make_option_iv(100, 0.30)]},
        ]
        result = _compute_term_structure(chains, 100)
        assert result["shape"] in ("CONTANGO", "MILD_CONTANGO")
        assert result["slope"] > 0

    def test_backwardation(self):
        """Shorter-dated IV > longer → backwardation."""
        chains = [
            {"dte": 5, "expiration": "2025-01-05",
             "calls": [make_option_iv(100, 0.40)], "puts": [make_option_iv(100, 0.40)]},
            {"dte": 30, "expiration": "2025-01-30",
             "calls": [make_option_iv(100, 0.25)], "puts": [make_option_iv(100, 0.25)]},
        ]
        result = _compute_term_structure(chains, 100)
        assert result["shape"] in ("BACKWARDATION", "MILD_BACKWARDATION")
        assert result["slope"] < 0

    def test_single_expiration(self):
        chains = [
            {"dte": 10, "expiration": "2025-01-10",
             "calls": [make_option_iv(100, 0.25)], "puts": [make_option_iv(100, 0.25)]},
        ]
        result = _compute_term_structure(chains, 100)
        assert result["shape"] == "UNKNOWN"

    def test_empty(self):
        result = _compute_term_structure([], 100)
        assert result["shape"] == "UNKNOWN"


class TestComputeVrp:
    def test_positive_vrp_high_iv(self):
        """High IV, low HV → positive VRP (expensive)."""
        closes = _make_multiplicative_prices(100, 100, daily_vol=0.005, seed=42)
        result = _compute_vrp(0.50, closes, 10, "POSITIVE_GAMMA", 1000, "LAMINAR")
        assert result["context"] != "N/A"
        assert result["vrp_raw"] > 0

    def test_negative_vrp_cheap_iv(self):
        """Low IV, high HV → negative VRP (cheap)."""
        closes = _make_multiplicative_prices(100, 100, daily_vol=0.03, seed=42)
        result = _compute_vrp(0.10, closes, 10, "NEGATIVE_GAMMA", -500, "TURBULENT")
        assert result["vrp_raw"] < 0

    def test_gex_vol_multiplier_pos_laminar(self):
        closes = _make_multiplicative_prices(100, 100, daily_vol=0.01, seed=42)
        result = _compute_vrp(0.30, closes, 10, "POSITIVE_GAMMA", 1000, "LAMINAR")
        assert result["gex_vol_mult"] == 0.78

    def test_gex_vol_multiplier_neg_turbulent(self):
        closes = _make_multiplicative_prices(100, 100, daily_vol=0.01, seed=42)
        result = _compute_vrp(0.30, closes, 10, "NEGATIVE_GAMMA", -500, "TURBULENT")
        assert result["gex_vol_mult"] == 1.15

    def test_insufficient_data(self):
        result = _compute_vrp(0.30, [100] * 10, 10, "POSITIVE_GAMMA", 0, "LAMINAR")
        assert result["context"] == "N/A"

    def test_daily_drag_floored_at_5_days(self):
        closes = list(np.cumsum(np.random.RandomState(42).randn(100) * 0.01) + 100)
        result = _compute_vrp(0.30, closes, 1, "POSITIVE_GAMMA", 0, "LAMINAR")
        # dte=1 but days should be max(1, 5) = 5
        assert result["daily_vrp_drag"] != 0 or result["vrp_gex_adjusted"] == 0


class TestComputeVolEdge:
    def test_cheap_iv_high_score(self):
        iv_hv = {"iv_hv_ratio": 0.70, "context": "CHEAP", "iv_percentile_proxy": 20}
        skew = {"regime": "FLAT"}
        term = {"shape": "CONTANGO"}
        result = _compute_vol_edge(iv_hv, skew, term, 10)
        assert result["score"] >= 60
        assert result["verdict"] == "STRONG_BUY_VOL"

    def test_expensive_iv_low_score(self):
        iv_hv = {"iv_hv_ratio": 1.8, "context": "VERY_EXPENSIVE", "iv_percentile_proxy": 80}
        skew = {"regime": "EXTREME_CALL_SKEW"}
        term = {"shape": "BACKWARDATION"}
        result = _compute_vol_edge(iv_hv, skew, term, 10)
        assert result["score"] <= 10
        assert result["verdict"] in ("EXPENSIVE_VOL", "AVOID_BUYING")

    def test_score_clamped(self):
        iv_hv = {"iv_hv_ratio": 0.50, "context": "CHEAP", "iv_percentile_proxy": 5}
        skew = {"regime": "FLAT"}
        term = {"shape": "CONTANGO"}
        vrp = {"context": "DISCOUNT", "vrp_gex_adjusted": -5}
        result = _compute_vol_edge(iv_hv, skew, term, 10, vrp)
        assert 0 <= result["score"] <= 100

    def test_vrp_discount_boosts_score(self):
        iv_hv = {"iv_hv_ratio": 1.0, "context": "FAIR", "iv_percentile_proxy": 50}
        skew = {"regime": "MODERATE_PUT_SKEW"}
        term = {"shape": "FLAT"}
        score_no_vrp = _compute_vol_edge(iv_hv, skew, term, 10)["score"]
        vrp = {"context": "DISCOUNT", "vrp_gex_adjusted": -3}
        score_with_vrp = _compute_vol_edge(iv_hv, skew, term, 10, vrp)["score"]
        assert score_with_vrp > score_no_vrp


class TestComputeVolAnalysis:
    def test_full_pipeline(self):
        calls, puts = make_chain(100)
        hist = make_price_history(100, 250)
        result = compute_vol_analysis(calls, puts, 100, 10, hist, atm_iv=0.25)
        assert "iv_hv" in result
        assert "skew" in result
        assert "vrp" in result
        assert "vol_edge" in result

    def test_with_gex_regime(self):
        calls, puts = make_chain(100)
        hist = make_price_history(100, 250)
        result = compute_vol_analysis(
            calls, puts, 100, 10, hist, atm_iv=0.25,
            gex_regime="NEGATIVE_GAMMA", total_gex=-500, reynolds_regime="TURBULENT",
        )
        assert result["vrp"]["gex_vol_mult"] == 1.15


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_option_iv(strike, iv):
    return {"strike": strike, "impliedVolatility": iv, "openInterest": 100, "volume": 50}


def _make_multiplicative_prices(start, n, daily_vol=0.01, seed=42):
    """Generate prices via multiplicative random walk (proper % returns)."""
    rng = np.random.RandomState(seed)
    prices = [float(start)]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + rng.randn() * daily_vol))
    return prices
