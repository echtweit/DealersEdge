"""Tests for max_pain — max pain calculation and OI wall detection."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from max_pain import calculate_max_pain, find_oi_walls
from tests.conftest import make_option


class TestCalculateMaxPain:
    def test_simple_three_strikes(self):
        calls = [
            make_option(95, oi=100),
            make_option(100, oi=500),
            make_option(105, oi=100),
        ]
        puts = [
            make_option(95, oi=100),
            make_option(100, oi=500),
            make_option(105, oi=100),
        ]
        result = calculate_max_pain(calls, puts)
        assert result["max_pain"] == 100

    def test_max_pain_at_heavy_call_oi(self):
        """Heavy call OI at 110 → max pain should be at or below 110."""
        calls = [
            make_option(100, oi=10),
            make_option(105, oi=10),
            make_option(110, oi=10000),
        ]
        puts = [
            make_option(100, oi=10),
            make_option(105, oi=10),
            make_option(110, oi=10),
        ]
        result = calculate_max_pain(calls, puts)
        assert result["max_pain"] <= 110

    def test_empty_chains(self):
        result = calculate_max_pain([], [])
        assert result["max_pain"] == 0
        assert result["pain_by_strike"] == []

    def test_pain_by_strike_populated(self):
        calls = [make_option(95, oi=100), make_option(100, oi=200)]
        puts = [make_option(95, oi=200), make_option(100, oi=100)]
        result = calculate_max_pain(calls, puts)
        assert len(result["pain_by_strike"]) == 2
        assert all("total_pain" in p for p in result["pain_by_strike"])

    def test_single_strike(self):
        calls = [make_option(100, oi=500)]
        puts = [make_option(100, oi=500)]
        result = calculate_max_pain(calls, puts)
        assert result["max_pain"] == 100
        assert result["pain_by_strike"][0]["total_pain"] == 0


class TestFindOiWalls:
    def test_basic_walls(self):
        calls = [
            make_option(105, oi=100),
            make_option(110, oi=500),  # call wall
            make_option(115, oi=200),
        ]
        puts = [
            make_option(85, oi=100),
            make_option(90, oi=600),  # put wall
            make_option(95, oi=300),
        ]
        result = find_oi_walls(calls, puts, 100)
        assert result["call_wall"]["strike"] == 110
        assert result["call_wall"]["oi"] == 500
        assert result["put_wall"]["strike"] == 90
        assert result["put_wall"]["oi"] == 600

    def test_no_calls_above(self):
        calls = [make_option(95, oi=500)]
        puts = [make_option(90, oi=300)]
        result = find_oi_walls(calls, puts, 100)
        assert result["call_wall"]["strike"] == 0

    def test_no_puts_below(self):
        calls = [make_option(105, oi=500)]
        puts = [make_option(105, oi=300)]
        result = find_oi_walls(calls, puts, 100)
        assert result["put_wall"]["strike"] == 0

    def test_top_walls_limited(self):
        calls = [make_option(100 + i, oi=100 * i) for i in range(1, 10)]
        puts = [make_option(100 - i, oi=100 * i) for i in range(1, 10)]
        result = find_oi_walls(calls, puts, 100)
        assert len(result["top_call_walls"]) <= 5
        assert len(result["top_put_walls"]) <= 5

    def test_ignores_far_otm_lottery_put_wall_when_near_exists(self):
        # Very large OI far below spot should not dominate near-spot wall selection.
        spot = 100
        calls = [
            make_option(105, oi=300),
            make_option(110, oi=400),
        ]
        puts = [
            make_option(95, oi=800),    # near-spot, should win
            make_option(80, oi=300),    # in-band
            make_option(50, oi=10000),  # far OTM outlier (outside 20% band)
        ]
        result = find_oi_walls(calls, puts, spot)
        assert result["put_wall"]["strike"] == 95
        assert result["put_wall_scope"] == "near_spot"

    def test_falls_back_to_full_chain_when_no_near_walls(self):
        spot = 100
        calls = [make_option(140, oi=1200)]  # no calls inside +20% band
        puts = [make_option(40, oi=900)]      # no puts inside -20% band
        result = find_oi_walls(calls, puts, spot)
        assert result["call_wall"]["strike"] == 140
        assert result["put_wall"]["strike"] == 40
        assert result["call_wall_scope"] == "full_chain"
        assert result["put_wall_scope"] == "full_chain"
