"""Tests for gex_calculator — Black-Scholes greeks, GEX profile, entropy, flip point."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest
from gex_calculator import (
    black_scholes_gamma,
    black_scholes_delta,
    black_scholes_charm,
    black_scholes_vanna,
    calculate_gex_profile,
    compute_gex_entropy,
    _find_gex_flip,
)
from tests.conftest import make_chain, make_option


class TestBlackScholesGamma:
    def test_atm_gamma_is_positive(self):
        g = black_scholes_gamma(100, 100, 0.1, 0.05, 0.3)
        assert g > 0

    def test_gamma_peaks_at_atm(self):
        g_atm = black_scholes_gamma(100, 100, 0.1, 0.05, 0.3)
        g_otm = black_scholes_gamma(100, 120, 0.1, 0.05, 0.3)
        g_itm = black_scholes_gamma(100, 80, 0.1, 0.05, 0.3)
        assert g_atm > g_otm
        assert g_atm > g_itm

    def test_zero_time_returns_zero(self):
        assert black_scholes_gamma(100, 100, 0, 0.05, 0.3) == 0.0

    def test_zero_vol_returns_zero(self):
        assert black_scholes_gamma(100, 100, 0.1, 0.05, 0) == 0.0

    def test_zero_spot_returns_zero(self):
        assert black_scholes_gamma(0, 100, 0.1, 0.05, 0.3) == 0.0

    def test_known_value(self):
        """ATM, 1yr, 30% vol, 5% rate — should be roughly 0.0132."""
        g = black_scholes_gamma(100, 100, 1.0, 0.05, 0.3)
        assert 0.01 < g < 0.02


class TestBlackScholesDelta:
    def test_call_delta_between_0_and_1(self):
        d = black_scholes_delta(100, 100, 0.1, 0.05, 0.3, "call")
        assert 0 < d < 1

    def test_put_delta_between_neg1_and_0(self):
        d = black_scholes_delta(100, 100, 0.1, 0.05, 0.3, "put")
        assert -1 < d < 0

    def test_deep_itm_call_near_1(self):
        d = black_scholes_delta(100, 50, 0.1, 0.05, 0.3, "call")
        assert d > 0.95

    def test_deep_otm_call_near_0(self):
        d = black_scholes_delta(100, 200, 0.1, 0.05, 0.3, "call")
        assert d < 0.05

    def test_zero_inputs(self):
        assert black_scholes_delta(0, 100, 0.1, 0.05, 0.3, "call") == 0.0
        assert black_scholes_delta(100, 100, 0, 0.05, 0.3, "put") == 0.0


class TestBlackScholesCharm:
    def test_returns_float(self):
        c = black_scholes_charm(100, 100, 0.1, 0.05, 0.3, "call")
        assert isinstance(c, float)

    def test_zero_time_returns_zero(self):
        assert black_scholes_charm(100, 100, 0, 0.05, 0.3, "call") == 0.0

    def test_zero_vol_returns_zero(self):
        assert black_scholes_charm(100, 100, 0.1, 0.05, 0, "call") == 0.0


class TestBlackScholesVanna:
    def test_returns_float(self):
        v = black_scholes_vanna(100, 100, 0.1, 0.05, 0.3)
        assert isinstance(v, float)

    def test_zero_inputs(self):
        assert black_scholes_vanna(0, 100, 0.1, 0.05, 0.3) == 0.0
        assert black_scholes_vanna(100, 100, 0, 0.05, 0.3) == 0.0


class TestCalculateGexProfile:
    def test_basic_profile(self):
        calls, puts = make_chain(100, n_strikes=11)
        result = calculate_gex_profile(calls, puts, 100, 10)
        assert "gex_by_strike" in result
        assert "total_gex" in result
        assert "flip_point" in result
        assert "regime" in result
        assert "entropy" in result
        assert len(result["gex_by_strike"]) == 11

    def test_regime_is_valid(self):
        calls, puts = make_chain(100)
        result = calculate_gex_profile(calls, puts, 100, 10)
        assert result["regime"] in ("POSITIVE_GAMMA", "NEGATIVE_GAMMA")

    def test_total_gex_is_sum(self):
        calls, puts = make_chain(100, n_strikes=5)
        result = calculate_gex_profile(calls, puts, 100, 10)
        expected = round(result["total_call_gex"] + result["total_put_gex"], 2)
        assert abs(result["total_gex"] - expected) < 0.1

    def test_zero_dte_doesnt_crash(self):
        calls, puts = make_chain(100, n_strikes=5)
        result = calculate_gex_profile(calls, puts, 100, 0)
        assert result["total_gex"] != 0 or result["total_gex"] == 0

    def test_empty_chains(self):
        result = calculate_gex_profile([], [], 100, 10)
        assert result["gex_by_strike"] == []
        assert result["total_gex"] == 0
        assert result["abs_gamma_strike"] == 100

    def test_single_strike(self):
        calls = [make_option(100, oi=500)]
        puts = [make_option(100, oi=500)]
        result = calculate_gex_profile(calls, puts, 100, 10)
        assert len(result["gex_by_strike"]) == 1

    def test_per_strike_has_all_fields(self):
        calls, puts = make_chain(100, n_strikes=3)
        result = calculate_gex_profile(calls, puts, 100, 10)
        expected_keys = {
            "strike", "call_oi", "put_oi", "call_gex", "put_gex", "net_gex",
            "call_delta", "put_delta", "net_dealer_delta",
            "call_charm", "put_charm", "net_charm",
            "call_vanna", "put_vanna", "net_vanna",
        }
        actual_keys = set(result["gex_by_strike"][0].keys())
        assert expected_keys == actual_keys


class TestGexEntropy:
    def test_dispersed_entropy(self):
        """Many equal-weight strikes → high entropy → DISPERSED."""
        gex = [{"strike": 90 + i, "net_gex": 100} for i in range(20)]
        result = compute_gex_entropy(gex, 100)
        assert result["regime"] == "DISPERSED"
        assert result["entropy_norm"] > 0.7

    def test_concentrated_entropy(self):
        """One dominant strike → low entropy → CRITICAL."""
        gex = [{"strike": 100, "net_gex": 10000}]
        gex += [{"strike": 90 + i, "net_gex": 1} for i in range(10)]
        result = compute_gex_entropy(gex, 100)
        assert result["regime"] in ("CRITICAL", "APPROACHING")
        assert result["entropy_norm"] < 0.5

    def test_empty_list(self):
        result = compute_gex_entropy([], 100)
        assert result["regime"] == "DISPERSED"
        assert result["n_strikes"] == 0

    def test_too_few_strikes(self):
        gex = [{"strike": 100, "net_gex": 100}, {"strike": 101, "net_gex": 50}]
        result = compute_gex_entropy(gex, 100)
        assert result["regime"] == "DISPERSED"

    def test_zero_gex(self):
        gex = [{"strike": 95 + i, "net_gex": 0} for i in range(10)]
        result = compute_gex_entropy(gex, 100)
        assert result["regime"] == "DISPERSED"


class TestFindGexFlip:
    def test_basic_crossing(self):
        gex = [
            {"strike": 95, "net_gex": -100},
            {"strike": 100, "net_gex": 100},
        ]
        flip = _find_gex_flip(gex, 100)
        assert flip is not None
        assert 95 < flip < 100

    def test_no_crossing(self):
        gex = [
            {"strike": 95, "net_gex": 100},
            {"strike": 100, "net_gex": 200},
        ]
        flip = _find_gex_flip(gex, 100)
        assert flip is None

    def test_single_strike(self):
        gex = [{"strike": 100, "net_gex": 100}]
        flip = _find_gex_flip(gex, 100)
        assert flip is None

    def test_multiple_crossings_picks_nearest_to_spot(self):
        gex = [
            {"strike": 80, "net_gex": -100},
            {"strike": 85, "net_gex": 50},
            {"strike": 95, "net_gex": -20},
            {"strike": 100, "net_gex": 80},
        ]
        flip = _find_gex_flip(gex, 98)
        assert flip is not None
        assert flip > 90  # should be the crossing nearest to 98
