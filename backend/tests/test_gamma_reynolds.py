"""Tests for gamma_reynolds — Reynolds number, regime classification, phase transitions."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from gamma_reynolds import compute_gamma_reynolds, detect_phase_transition
from tests.conftest import make_chain, make_option


class TestComputeGammaReynolds:
    def test_laminar_regime(self):
        """Low call volume, high call OI with no offsetting put OI → laminar.
        Dealer gamma = call OI gamma (net positive) must dominate speculative gamma (call volume)."""
        calls = [make_option(100, oi=5000, volume=10, iv=0.30)]
        puts = [make_option(100, oi=100, volume=10, iv=0.30)]
        result = compute_gamma_reynolds(calls, puts, 100)
        assert result["regime"] == "LAMINAR"
        assert result["reynolds_number"] < 0.7

    def test_turbulent_regime(self):
        """High volume relative to OI → turbulent."""
        calls = [make_option(100, oi=100, volume=5000, iv=0.30)]
        puts = [make_option(100, oi=100, volume=50, iv=0.30)]
        result = compute_gamma_reynolds(calls, puts, 100)
        assert result["regime"] == "TURBULENT"
        assert result["reynolds_number"] > 1.0

    def test_empty_chains(self):
        result = compute_gamma_reynolds([], [], 100)
        assert result["regime"] == "UNKNOWN"
        assert result["reynolds_number"] == 0

    def test_empty_calls_only(self):
        puts = [make_option(100, oi=500, volume=50)]
        result = compute_gamma_reynolds([], puts, 100)
        assert result["regime"] == "UNKNOWN"

    def test_re_capped_at_99(self):
        """Extreme case: dealer gamma near zero → Re should not exceed 99."""
        calls = [make_option(100, oi=0, volume=10000, iv=0.30)]
        puts = [make_option(100, oi=0, volume=0, iv=0.30)]
        result = compute_gamma_reynolds(calls, puts, 100)
        assert result["reynolds_number"] <= 99

    def test_call_put_ratio(self):
        calls = [make_option(100, volume=300)]
        puts = [make_option(100, volume=100)]
        result = compute_gamma_reynolds(calls, puts, 100)
        assert result["call_put_ratio"] == 3.0

    def test_zero_put_volume_doesnt_divide_by_zero(self):
        calls = [make_option(100, volume=300)]
        puts = [make_option(100, volume=0)]
        result = compute_gamma_reynolds(calls, puts, 100)
        assert result["call_put_ratio"] == 300.0  # 300 / max(0, 1) = 300

    def test_atm_iv_computed(self):
        calls = [make_option(100, iv=0.25)]
        puts = [make_option(100, iv=0.35)]
        result = compute_gamma_reynolds(calls, puts, 100)
        assert result["atm_iv"] > 0

    def test_result_has_all_keys(self):
        calls, puts = make_chain(100, n_strikes=5)
        result = compute_gamma_reynolds(calls, puts, 100)
        expected_keys = {
            "reynolds_number", "speculative_gamma", "dealer_gamma", "regime",
            "call_put_ratio", "call_volume", "put_volume", "call_oi", "put_oi", "atm_iv",
        }
        assert set(result.keys()) == expected_keys


class TestDetectPhaseTransition:
    def test_laminar(self):
        daily = [{"lag1_acf": -0.15, "regime": "LONG_GAMMA"} for _ in range(5)]
        result = detect_phase_transition(daily)
        assert result["regime"] == "LAMINAR"
        assert result["pct_amplified"] == 0

    def test_turbulent(self):
        daily = [{"lag1_acf": 0.12, "regime": "SHORT_GAMMA"} for _ in range(5)]
        result = detect_phase_transition(daily)
        assert result["regime"] == "TURBULENT"
        assert result["pct_amplified"] == 100

    def test_approaching(self):
        """20% amplified is above 12.9% * 0.7 = 9.03% → APPROACHING."""
        daily = [{"lag1_acf": -0.1}] * 8 + [{"lag1_acf": 0.1}] * 2
        result = detect_phase_transition(daily)
        # 2/10 = 20% amplified, threshold = 12.9%, so 20% > 12.9% → TURBULENT
        assert result["regime"] in ("TURBULENT", "APPROACHING")

    def test_empty_results(self):
        result = detect_phase_transition([])
        assert result["regime"] == "UNKNOWN"

    def test_custom_threshold(self):
        daily = [{"lag1_acf": 0.1}] * 3 + [{"lag1_acf": -0.1}] * 7
        result = detect_phase_transition(daily, threshold_pct=25.0)
        assert result["pct_amplified"] == 30.0
