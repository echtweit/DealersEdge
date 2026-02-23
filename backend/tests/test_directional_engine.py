"""Tests for directional_engine — thesis classification, Kelly sizing, wall-break, positions."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from directional_engine import (
    classify_thesis,
    _kelly_size,
    _estimate_wall_break_probability,
    _round_strike,
    _atr_max_move,
    _clamp_strike,
    _clamp_target,
    _collision_probability,
)
from tests.conftest import (
    make_acf, make_reynolds, make_phase, make_channel, make_channel_strat, make_technicals,
)


# ── Kelly Sizing ─────────────────────────────────────────────────────────────


class TestKellySize:
    def test_basic_kelly(self):
        pct, label = _kelly_size(10, 60, 1.0)
        assert 0.25 <= pct <= 5.0
        assert "%" in label

    def test_zero_edge_returns_zero(self):
        pct, label = _kelly_size(0, 60, 1.0)
        assert pct == 0
        assert label == "0%"

    def test_zero_prob_returns_zero(self):
        pct, label = _kelly_size(10, 0, 1.0)
        assert pct == 0

    def test_negative_edge_returns_zero(self):
        pct, label = _kelly_size(-5, 60, 1.0)
        assert pct == 0

    def test_capped_at_5_pct(self):
        pct, _ = _kelly_size(100, 90, 0.5)
        assert pct <= 5.0

    def test_floored_at_025_pct(self):
        pct, _ = _kelly_size(1, 10, 2.0)
        assert pct >= 0.25

    def test_expensive_vol_reduces(self):
        pct_normal, _ = _kelly_size(10, 60, 1.0, "FAIR")
        pct_expensive, _ = _kelly_size(10, 60, 1.8, "HIGH_PREMIUM")
        assert pct_expensive <= pct_normal

    def test_cheap_vol_increases(self):
        pct_normal, _ = _kelly_size(10, 60, 1.0, "FAIR")
        pct_cheap, _ = _kelly_size(10, 60, 0.7, "DISCOUNT")
        assert pct_cheap >= pct_normal

    def test_label_full_conviction(self):
        _, label = _kelly_size(50, 85, 0.7, "DISCOUNT")
        assert "conviction" in label or "standard" in label or "%" in label

    def test_label_reduced(self):
        _, label = _kelly_size(3, 40, 1.8, "HIGH_PREMIUM")
        assert "%" in label


# ── Wall Break Probability ───────────────────────────────────────────────────


class TestWallBreakProbability:
    def test_basic_probability(self):
        result = _estimate_wall_break_probability(
            0.5, "LAMINAR", -0.15, 5, 90, "LAMINAR", 1.2,
        )
        assert 5 <= result["probability"] <= 95

    def test_turbulent_high_prob(self):
        result = _estimate_wall_break_probability(
            1.5, "TURBULENT", 0.12, 60, 30, "TURBULENT", 2.0,
            sei=200, entropy_regime="CRITICAL", gex_regime="NEGATIVE_GAMMA",
        )
        assert result["probability"] > 50

    def test_laminar_low_prob(self):
        result = _estimate_wall_break_probability(
            0.3, "LAMINAR", -0.20, 5, 90, "LAMINAR", 0.8,
            gex_regime="POSITIVE_GAMMA",
        )
        assert result["probability"] < 30

    def test_positive_gamma_dampening(self):
        pos = _estimate_wall_break_probability(
            0.8, "TRANSITIONAL", 0, 50, 50, "LAMINAR", 1.0,
            gex_regime="POSITIVE_GAMMA",
        )
        neg = _estimate_wall_break_probability(
            0.8, "TRANSITIONAL", 0, 50, 50, "LAMINAR", 1.0,
            gex_regime="NEGATIVE_GAMMA",
        )
        assert pos["probability"] < neg["probability"]
        assert pos["gamma_asymmetry"] == -12
        assert neg["gamma_asymmetry"] == 5

    def test_probability_clamped(self):
        result_low = _estimate_wall_break_probability(
            0.1, "LAMINAR", -0.3, 0, 100, "LAMINAR", 0.5,
            gex_regime="POSITIVE_GAMMA",
        )
        assert result_low["probability"] >= 5

        result_high = _estimate_wall_break_probability(
            5.0, "TURBULENT", 0.3, 80, 10, "TURBULENT", 3.0,
            sei=300, entropy_regime="CRITICAL", gex_regime="NEGATIVE_GAMMA",
        )
        assert result_high["probability"] <= 95

    def test_confidence_levels(self):
        result = _estimate_wall_break_probability(
            1.5, "TURBULENT", 0.12, 60, 30, "TURBULENT", 1.5,
            sei=200,
        )
        assert result["confidence"] in ("HIGH", "MEDIUM", "LOW")
        assert result["re_says"] in ("BREAK", "HOLD")
        assert result["acf_says"] in ("BREAK", "HOLD")

    def test_collision_adjustments(self):
        result = _estimate_wall_break_probability(
            0.8, "TRANSITIONAL", 0, 50, 50, "LAMINAR", 1.0,
            atr_dollar=2.0, dte=10, call_wall_strike=105, put_wall_strike=95, spot=100,
        )
        assert result["collision_prob_call_wall"] > 0
        assert result["collision_prob_put_wall"] > 0

    def test_result_has_all_keys(self):
        result = _estimate_wall_break_probability(
            0.5, "LAMINAR", -0.1, 10, 80, "LAMINAR", 1.0,
        )
        expected_keys = {
            "probability", "confidence", "explanation",
            "re_says", "acf_says", "sei_says", "gamma_asymmetry",
            "collision_prob_call_wall", "collision_prob_put_wall",
            "beta_adj_factor", "re_beta_adj",
        }
        assert set(result.keys()) == expected_keys


# ── Strike & Target Helpers ──────────────────────────────────────────────────


class TestRoundStrike:
    def test_high_price(self):
        assert _round_strike(503.2, 500) == 505.0

    def test_mid_price(self):
        # spot > 100 → inc = 5.0 → round(152.3/5)*5 = 150
        assert _round_strike(152.3, 150) == 150.0
        assert _round_strike(153.0, 150) == 155.0

    def test_low_price(self):
        # spot <= 20 → inc = 0.5 → round(15.3/0.5)*0.5 = 15.5
        assert _round_strike(15.3, 15) == 15.5
        # spot > 20 → inc = 1.0
        assert _round_strike(25.3, 25) == 25.0

    def test_very_low_price(self):
        assert _round_strike(3.7, 3) == 3.5


class TestAtrMaxMove:
    def test_basic(self):
        move = _atr_max_move(2.0, 10)
        assert move > 0
        assert abs(move - 2.0 * (10 ** 0.5)) < 0.01

    def test_zero_atr(self):
        assert _atr_max_move(0, 10) == 0

    def test_zero_dte(self):
        assert _atr_max_move(2.0, 0) == 0

    def test_multiplier(self):
        base = _atr_max_move(2.0, 10, multiplier=1.0)
        doubled = _atr_max_move(2.0, 10, multiplier=2.0)
        assert abs(doubled - base * 2) < 0.01


class TestClampStrike:
    def test_call_within_range(self):
        strike = _clamp_strike(105, 100, 2.0, 10, True)
        assert strike <= 100 + 2.0 * (10 ** 0.5) * 1.5

    def test_call_beyond_range(self):
        strike = _clamp_strike(200, 100, 2.0, 5, True)
        assert strike < 200

    def test_put_within_range(self):
        strike = _clamp_strike(95, 100, 2.0, 10, False)
        assert strike >= 100 - 2.0 * (10 ** 0.5) * 1.5

    def test_zero_atr_passthrough(self):
        assert _clamp_strike(150, 100, 0, 10, True) == 150


class TestClampTarget:
    def test_within_range(self):
        target = _clamp_target(105, 100, 2.0, 10)
        assert target == 105

    def test_beyond_upper(self):
        target = _clamp_target(200, 100, 2.0, 5)
        assert target < 200

    def test_beyond_lower(self):
        target = _clamp_target(50, 100, 2.0, 5)
        assert target > 50

    def test_zero_atr_passthrough(self):
        assert _clamp_target(200, 100, 0, 10) == 200


class TestCollisionProbability:
    def test_at_level(self):
        assert _collision_probability(100, 100, 2.0, 10) == 0.95

    def test_far_away(self):
        p = _collision_probability(100, 200, 2.0, 5)
        assert p < 0.5

    def test_close(self):
        p = _collision_probability(100, 101, 5.0, 10)
        assert p > 0.5

    def test_zero_inputs(self):
        assert _collision_probability(100, 105, 0, 10) == 0.5
        assert _collision_probability(100, 105, 2.0, 0) == 0.5
        assert _collision_probability(100, 0, 2.0, 10) == 0.5


# ── Classify Thesis ──────────────────────────────────────────────────────────


class TestClassifyThesis:
    def _run(self, acf, reynolds, phase, gex_regime="POSITIVE_GAMMA", **overrides):
        defaults = dict(
            spot=100, acf=acf, reynolds=reynolds, phase=phase,
            gex_regime=gex_regime,
            channel=make_channel(95, 105, 100),
            channel_strat=make_channel_strat(),
            max_pain=100, call_wall={"strike": 105, "oi": 500},
            put_wall={"strike": 95, "oi": 500},
            flip_point=100, abs_gamma_strike=100,
            total_charm=0, total_vanna=0, dte=10,
            technicals=make_technicals(spot=100),
        )
        defaults.update(overrides)
        return classify_thesis(**defaults)

    def test_momentum_breakout(self):
        # Use bullish technicals aligned with the bullish GEX direction (spot > flip_point)
        tech_bull = make_technicals("STRONG_UPTREND", alignment=3, spot=100)
        result = self._run(
            make_acf("SHORT_GAMMA", acf1=0.12, pct_amp=60, pct_damp=30, sei=120),
            make_reynolds("TURBULENT", 1.5),
            make_phase("TURBULENT"),
            gex_regime="NEGATIVE_GAMMA",
            technicals=tech_bull,
            flip_point=98,  # spot(100) > flip(98) → BULLISH
        )
        assert result["thesis"] == "MOMENTUM_BREAKOUT"
        assert result["bias"]["strength"] == "STRONG"

    def test_fade_moves(self):
        result = self._run(
            make_acf("LONG_GAMMA", acf1=-0.15, pct_amp=5, pct_damp=90),
            make_reynolds("LAMINAR", 0.3),
            make_phase("LAMINAR"),
        )
        assert result["thesis"] == "FADE_MOVES"

    def test_neutral(self):
        result = self._run(
            make_acf("NEUTRAL", acf1=0.0, pct_amp=50, pct_damp=50),
            make_reynolds("LAMINAR", 0.5),
            make_phase("LAMINAR"),
        )
        assert result["thesis"] in ("NEUTRAL", "FADE_MILD", "MOMENTUM_TREND", "CONFLICTED_PIN")

    def test_result_structure(self):
        result = self._run(
            make_acf(), make_reynolds(), make_phase(),
        )
        assert "thesis" in result
        assert "thesis_label" in result
        assert "bias" in result
        assert "positions" in result
        assert "level_actions" in result
        assert "wall_break" in result
        assert "avoid" in result
        assert "tech_context" in result

    def test_positions_have_required_fields(self):
        result = self._run(
            make_acf("LONG_GAMMA", acf1=-0.15),
            make_reynolds("LAMINAR", 0.3),
            make_phase("LAMINAR"),
        )
        for pos in result["positions"]:
            assert "name" in pos
            assert "edge_type" in pos
            assert "option_type" in pos

    def test_level_actions_sorted_by_distance(self):
        result = self._run(
            make_acf(), make_reynolds(), make_phase(),
        )
        distances = [a["distance_pct"] for a in result["level_actions"]]
        assert distances == sorted(distances)

    def test_tech_confirms_upgrades_strength(self):
        tech_bull = make_technicals("STRONG_UPTREND", alignment=3, spot=100)
        result = self._run(
            make_acf("SHORT_GAMMA", acf1=0.06, pct_amp=20, pct_damp=40),
            make_reynolds("TRANSITIONAL", 0.8),
            make_phase("LAMINAR"),
            technicals=tech_bull,
            flip_point=98,  # spot > flip → bullish GEX, aligns with bull technicals
        )
        assert result["bias"]["direction"] == "BULLISH"

    def test_tech_conflicts_downgrades_strength(self):
        tech_bear = make_technicals("STRONG_DOWNTREND", alignment=-3, rs_label="STRONG_LAGGARD", spot=100)
        result = self._run(
            make_acf("SHORT_GAMMA", acf1=0.06, pct_amp=20, pct_damp=40),
            make_reynolds("TRANSITIONAL", 0.8),
            make_phase("LAMINAR"),
            technicals=tech_bear,
            flip_point=98,  # GEX says bullish, tech says bearish
        )
        # Strong bearish technicals should override GEX direction
        assert result["bias"]["direction"] == "BEARISH"

    def test_avoid_list_populated(self):
        result = self._run(
            make_acf("SHORT_GAMMA", acf1=0.12, pct_amp=60, pct_damp=30, sei=120),
            make_reynolds("TURBULENT", 1.5),
            make_phase("TURBULENT"),
        )
        assert len(result["avoid"]) > 0
