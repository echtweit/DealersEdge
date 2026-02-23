"""Tests for straddle_analyzer — scoring, verdicts, P/L scenarios, theta schedule."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from straddle_analyzer import (
    analyze_straddles,
    _build_straddle,
    _build_strangle,
    _compute_iv_vs_rv,
    _score_regime,
    _score_iv,
    _score_catalyst,
    _score_structural,
    _determine_verdict,
    _compute_move_probability,
    _compute_theta_schedule,
)
from tests.conftest import (
    make_chain, make_acf, make_reynolds, make_phase, make_channel,
    make_technicals, make_price_history,
)


# ── Straddle / Strangle Construction ─────────────────────────────────────────


class TestBuildStraddle:
    def test_basic_straddle(self):
        calls, puts = make_chain(100, n_strikes=11)
        result = _build_straddle(calls, puts, 100)
        assert result["total_cost"] > 0
        assert result["upper_breakeven"] > result["strike"]
        assert result["lower_breakeven"] < result["strike"]
        assert result["required_move_pct"] > 0

    def test_empty_chains(self):
        result = _build_straddle([], [], 100)
        assert result["total_cost"] == 0


class TestBuildStrangle:
    def test_basic_strangle(self):
        calls, puts = make_chain(100, n_strikes=21)
        result = _build_strangle(calls, puts, 100)
        assert result["total_cost"] > 0
        assert result["call_strike"] > 100
        assert result["put_strike"] < 100

    def test_width(self):
        calls, puts = make_chain(100, n_strikes=21)
        result = _build_strangle(calls, puts, 100)
        assert result["width"] > 0
        assert result["width_pct"] > 0


# ── Sub-Scores ───────────────────────────────────────────────────────────────


class TestScoreRegime:
    def test_turbulent_high_score(self):
        score = _score_regime(1.5, "TURBULENT", 0.12, 60, "NEGATIVE_GAMMA")
        assert score > 15

    def test_laminar_low_score(self):
        score = _score_regime(0.3, "LAMINAR", -0.15, 5, "POSITIVE_GAMMA")
        assert score < 5

    def test_clamped_0_25(self):
        score = _score_regime(0.1, "LAMINAR", -0.20, 0, "POSITIVE_GAMMA")
        assert 0 <= score <= 25


class TestScoreIv:
    def test_cheap_iv(self):
        score = _score_iv(0.70, 0.20)
        assert score >= 20

    def test_expensive_iv(self):
        score = _score_iv(1.5, 0.50)
        assert score <= 5

    def test_clamped(self):
        score = _score_iv(0.5, 0.10)
        assert 0 <= score <= 25


class TestScoreCatalyst:
    def test_turbulent_phase(self):
        phase = make_phase("TURBULENT", pct_amplified=20, distance=-5)
        acf = make_acf("SHORT_GAMMA", sei=200)
        score = _score_catalyst(phase, acf, 1.5)
        assert score > 10

    def test_calm_conditions(self):
        phase = make_phase("LAMINAR")
        acf = make_acf("LONG_GAMMA")
        score = _score_catalyst(phase, acf, 8.0)
        assert score < 10


class TestScoreStructural:
    def test_neg_gamma_tight_channel(self):
        ch = make_channel(98, 102, 100)
        straddle = {"required_move_pct": 2.0}
        score = _score_structural("NEGATIVE_GAMMA", ch, 1.5, straddle, 2.0, "CRITICAL")
        assert score > 15

    def test_pos_gamma_wide_channel(self):
        ch = make_channel(80, 120, 100)
        straddle = {"required_move_pct": 5.0}
        score = _score_structural("POSITIVE_GAMMA", ch, 0.3, straddle, 1.0, "DISPERSED")
        assert score < 15


# ── Verdict ──────────────────────────────────────────────────────────────────


class TestDetermineVerdict:
    def test_buy_straddle(self):
        verdict, label = _determine_verdict(75, 0.9, "TURBULENT", {"required_move_pct": 2})
        assert verdict == "BUY_STRADDLE"

    def test_avoid(self):
        verdict, label = _determine_verdict(20, 1.5, "LAMINAR", {"required_move_pct": 5})
        assert verdict == "AVOID"

    def test_consider(self):
        verdict, label = _determine_verdict(50, 1.1, "TRANSITIONAL", {"required_move_pct": 3})
        assert verdict == "CONSIDER"


# ── IV vs RV ─────────────────────────────────────────────────────────────────


class TestComputeIvVsRv:
    def test_cheap(self):
        hist = make_price_history(100, 100)
        result = _compute_iv_vs_rv(0.10, hist, 10)
        assert result["iv_context"] in ("CHEAP", "FAIR", "EXPENSIVE")

    def test_no_history(self):
        result = _compute_iv_vs_rv(0.30, None, 10)
        assert result["realized_vol"] == 0
        assert result["iv_rv_ratio"] == 1.0

    def test_short_history(self):
        result = _compute_iv_vs_rv(0.30, [{"close": 100}] * 5, 10)
        assert result["realized_vol"] == 0


# ── Move Probability ─────────────────────────────────────────────────────────


class TestComputeMoveProbability:
    def test_with_history(self):
        hist = make_price_history(100, 250)
        result = _compute_move_probability(hist, 10, 2.0)
        assert result["probability"] >= 0
        assert result["sample_size"] > 0
        assert len(result["windows"]) > 0

    def test_zero_breakeven(self):
        hist = make_price_history(100, 250)
        result = _compute_move_probability(hist, 10, 0)
        assert result["probability"] == 0

    def test_no_history(self):
        result = _compute_move_probability(None, 10, 2.0)
        assert result["probability"] == 0

    def test_short_history(self):
        hist = make_price_history(100, 10)
        result = _compute_move_probability(hist, 10, 2.0)
        assert result["probability"] == 0


# ── Theta Schedule ───────────────────────────────────────────────────────────


class TestComputeThetaSchedule:
    def test_basic_schedule(self):
        straddle = {"total_cost": 5.0}
        result = _compute_theta_schedule(straddle, 10, 0.30, 100)
        assert result["daily_theta"] > 0
        assert len(result["schedule"]) == 10
        assert result["schedule"][-1]["cumulative_decay_pct"] > result["schedule"][0]["cumulative_decay_pct"]

    def test_zero_cost(self):
        result = _compute_theta_schedule({"total_cost": 0}, 10, 0.30, 100)
        assert result["daily_theta"] == 0

    def test_schedule_capped_at_21(self):
        straddle = {"total_cost": 5.0}
        result = _compute_theta_schedule(straddle, 30, 0.30, 100)
        assert len(result["schedule"]) <= 21


# ── VRP Drag ─────────────────────────────────────────────────────────────────


class TestVrpDrag:
    def test_high_premium_reduces_score(self):
        calls, puts = make_chain(100)
        hist = make_price_history(100, 100)
        vrp_expensive = {"context": "HIGH_PREMIUM", "vrp_gex_adjusted": 8.0}
        vrp_cheap = {"context": "DISCOUNT", "vrp_gex_adjusted": -3.0}

        result_expensive = analyze_straddles(
            calls, puts, 100, 10,
            make_acf(), make_reynolds(), make_phase(), "POSITIVE_GAMMA", make_channel(),
            price_history=hist, vrp_data=vrp_expensive,
        )
        result_cheap = analyze_straddles(
            calls, puts, 100, 10,
            make_acf(), make_reynolds(), make_phase(), "POSITIVE_GAMMA", make_channel(),
            price_history=hist, vrp_data=vrp_cheap,
        )

        assert result_expensive["score"]["vrp_drag"] < 0
        assert result_cheap["score"]["vrp_drag"] > 0
        assert result_cheap["score"]["total"] > result_expensive["score"]["total"]


# ── Full Pipeline ────────────────────────────────────────────────────────────


class TestAnalyzeStraddles:
    def test_full_result(self):
        calls, puts = make_chain(100)
        hist = make_price_history(100, 250)
        result = analyze_straddles(
            calls, puts, 100, 10,
            make_acf(), make_reynolds(), make_phase(), "POSITIVE_GAMMA", make_channel(),
            price_history=hist,
        )
        assert "straddle" in result
        assert "strangle" in result
        assert "score" in result
        assert "verdict" in result
        assert "reasoning" in result
        assert "warnings" in result
        assert result["verdict"] in ("BUY_STRADDLE", "BUY_STRANGLE", "CONSIDER", "AVOID")

    def test_with_technicals(self):
        calls, puts = make_chain(100)
        hist = make_price_history(100, 250)
        tech = make_technicals(spot=100)
        result = analyze_straddles(
            calls, puts, 100, 10,
            make_acf(), make_reynolds(), make_phase(), "POSITIVE_GAMMA", make_channel(),
            price_history=hist, technicals=tech,
        )
        assert result["atr_context"]["atr_pct"] > 0
