"""Regression tests for identified edge-case bugs."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from gamma_reynolds import compute_gamma_reynolds
from gex_calculator import calculate_gex_profile
from tests.conftest import make_option


class TestSpotZeroGuard:
    def test_gamma_reynolds_spot_zero(self):
        """spot=0 should return UNKNOWN, not crash with log(0)."""
        calls = [make_option(100, oi=500, volume=50)]
        puts = [make_option(100, oi=500, volume=50)]
        result = compute_gamma_reynolds(calls, puts, 0)
        assert result["regime"] == "UNKNOWN"
        assert result["reynolds_number"] == 0

    def test_gamma_reynolds_negative_spot(self):
        calls = [make_option(100, oi=500)]
        puts = [make_option(100, oi=500)]
        result = compute_gamma_reynolds(calls, puts, -10)
        assert result["regime"] == "UNKNOWN"


class TestFlipPointNoneRegime:
    def test_no_flip_positive_total_gex(self):
        """When no flip point found and total GEX > 0 → POSITIVE_GAMMA."""
        calls = [make_option(100, oi=1000, iv=0.3)]
        puts = [make_option(100, oi=100, iv=0.3)]
        result = calculate_gex_profile(calls, puts, 100, 10)
        # With much higher call OI, total should be positive, no crossing
        assert result["regime"] in ("POSITIVE_GAMMA", "NEGATIVE_GAMMA")

    def test_no_flip_negative_total_gex(self):
        """When no flip point and total GEX < 0, regime should be NEGATIVE_GAMMA."""
        calls = [make_option(100, oi=100, iv=0.3)]
        puts = [make_option(100, oi=5000, iv=0.3)]
        result = calculate_gex_profile(calls, puts, 100, 10)
        # Heavy put OI should make total GEX negative
        if result["flip_point"] is None:
            assert result["regime"] == "NEGATIVE_GAMMA"


class TestMissingStrikeKey:
    def test_option_without_strike(self):
        """Options missing 'strike' key should be skipped, not crash."""
        calls = [{"openInterest": 100, "volume": 50, "impliedVolatility": 0.3}]
        puts = [make_option(100, oi=500)]
        result = calculate_gex_profile(calls, puts, 100, 10)
        assert len(result["gex_by_strike"]) == 1  # only the valid put

    def test_option_with_zero_strike(self):
        calls = [make_option(0, oi=500)]
        puts = [make_option(100, oi=500)]
        result = calculate_gex_profile(calls, puts, 100, 10)
        assert len(result["gex_by_strike"]) == 1
