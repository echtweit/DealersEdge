"""Tests for collision_time — first-passage-time estimates."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from collision_time import compute_collision_times


class TestComputeCollisionTimes:
    def test_basic_computation(self):
        levels = {"call_wall": 105, "put_wall": 95}
        result = compute_collision_times(100, levels, atr_dollar=2.0, acf_regime="NEUTRAL", reynolds_regime="LAMINAR", dte=10)
        assert len(result) == 2
        assert all("expected_days_raw" in r for r in result)
        assert all("urgency" in r for r in result)

    def test_closer_level_arrives_faster(self):
        levels = {"near": 101, "far": 110}
        result = compute_collision_times(100, levels, atr_dollar=2.0, acf_regime="NEUTRAL", reynolds_regime="LAMINAR", dte=10)
        near = next(r for r in result if r["level_label"] == "near")
        far = next(r for r in result if r["level_label"] == "far")
        assert near["expected_days_raw"] < far["expected_days_raw"]

    def test_turbulent_accelerates(self):
        levels = {"target": 105}
        laminar = compute_collision_times(100, levels, 2.0, "NEUTRAL", "LAMINAR", 10)
        turbulent = compute_collision_times(100, levels, 2.0, "NEUTRAL", "TURBULENT", 10)
        assert turbulent[0]["expected_days_adj"] < laminar[0]["expected_days_adj"]

    def test_long_gamma_decelerates(self):
        levels = {"target": 105}
        neutral = compute_collision_times(100, levels, 2.0, "NEUTRAL", "LAMINAR", 10)
        dampened = compute_collision_times(100, levels, 2.0, "LONG_GAMMA", "LAMINAR", 10)
        assert dampened[0]["expected_days_adj"] > neutral[0]["expected_days_adj"]

    def test_zero_atr(self):
        result = compute_collision_times(100, {"target": 105}, 0, "NEUTRAL", "LAMINAR", 10)
        assert result == []

    def test_zero_spot(self):
        result = compute_collision_times(0, {"target": 105}, 2.0, "NEUTRAL", "LAMINAR", 10)
        assert result == []

    def test_level_at_spot(self):
        levels = {"here": 100}
        result = compute_collision_times(100, levels, 2.0, "NEUTRAL", "LAMINAR", 10)
        assert result[0]["expected_days_raw"] == 0.0
        assert result[0]["urgency"] == "NOW"

    def test_none_levels_skipped(self):
        levels = {"valid": 105, "invalid": None, "zero": 0}
        result = compute_collision_times(100, levels, 2.0, "NEUTRAL", "LAMINAR", 10)
        assert len(result) == 1

    def test_probability_bounded(self):
        levels = {"target": 105}
        result = compute_collision_times(100, levels, 2.0, "NEUTRAL", "LAMINAR", 10)
        assert 1 <= result[0]["prob_within_dte"] <= 99

    def test_urgency_labels(self):
        levels = {"close": 100.01, "medium": 103, "far": 120}
        result = compute_collision_times(100, levels, 2.0, "NEUTRAL", "LAMINAR", 5)
        urgencies = {r["level_label"]: r["urgency"] for r in result}
        assert urgencies["close"] in ("NOW", "IMMINENT")

    def test_sorted_by_adjusted_time(self):
        levels = {"a": 110, "b": 102, "c": 105}
        result = compute_collision_times(100, levels, 2.0, "NEUTRAL", "LAMINAR", 10)
        times = [r["expected_days_adj"] for r in result]
        assert times == sorted(times)

    def test_first_passage_formula(self):
        """T = L² / (2σ²) for neutral regime."""
        levels = {"target": 104}
        result = compute_collision_times(100, levels, 2.0, "NEUTRAL", "LAMINAR", 10)
        expected = (4.0 ** 2) / (2 * 2.0 ** 2)  # 16 / 8 = 2.0
        assert abs(result[0]["expected_days_raw"] - expected) < 0.01
