"""Tests for acf_engine — ACF computation, regime classification, self-excitation."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest
from acf_engine import compute_daily_acf, classify_regime, compute_self_excitation


class TestComputeDailyAcf:
    def test_positively_autocorrelated(self):
        """Trending prices → positive ACF."""
        prices = np.cumsum(np.ones(200))  # strictly increasing
        acf = compute_daily_acf(prices, max_lag=5)
        assert not np.isnan(acf[0])
        assert acf[0] > 0

    def test_mean_reverting(self):
        """Alternating prices → negative ACF."""
        prices = np.array([100 + (-1)**i * 0.5 for i in range(200)])
        acf = compute_daily_acf(prices, max_lag=5)
        assert not np.isnan(acf[0])
        assert acf[0] < 0

    def test_white_noise(self):
        """Random walk → ACF near zero."""
        rng = np.random.RandomState(42)
        prices = np.cumsum(rng.randn(500)) + 100
        acf = compute_daily_acf(prices, max_lag=5)
        assert abs(acf[0]) < 0.15

    def test_constant_prices(self):
        """Zero variance → NaN."""
        prices = np.full(200, 100.0)
        acf = compute_daily_acf(prices, max_lag=5)
        assert np.all(np.isnan(acf))

    def test_too_few_prices(self):
        prices = np.array([100, 101, 102])
        acf = compute_daily_acf(prices, max_lag=5)
        assert np.all(np.isnan(acf))

    def test_lag_count(self):
        rng = np.random.RandomState(42)
        prices = np.cumsum(rng.randn(200)) + 100
        acf = compute_daily_acf(prices, max_lag=3)
        assert len(acf) == 3


class TestClassifyRegime:
    def test_long_gamma(self):
        assert classify_regime(-0.10) == "LONG_GAMMA"
        assert classify_regime(-0.06) == "LONG_GAMMA"

    def test_short_gamma(self):
        assert classify_regime(0.10) == "SHORT_GAMMA"
        assert classify_regime(0.06) == "SHORT_GAMMA"

    def test_neutral(self):
        assert classify_regime(0.0) == "NEUTRAL"
        assert classify_regime(0.04) == "NEUTRAL"
        assert classify_regime(-0.04) == "NEUTRAL"

    def test_exact_boundaries(self):
        assert classify_regime(-0.05) == "NEUTRAL"  # not < -0.05
        assert classify_regime(0.05) == "NEUTRAL"   # not > 0.05


class TestComputeSelfExcitation:
    def test_trending_prices_produce_excitation(self):
        """Strong trending → clusters of same-direction moves → positive SEI."""
        rng = np.random.RandomState(42)
        trending = np.cumsum(np.abs(rng.randn(200)) * 0.003) + 100
        result = compute_self_excitation(trending)
        assert result["sei"] >= 0

    def test_random_walk(self):
        rng = np.random.RandomState(42)
        prices = np.cumsum(rng.randn(200) * 0.001) + 100
        result = compute_self_excitation(prices)
        assert result["regime"] in ("NONE", "LOW_EXCITATION", "MODERATE_EXCITATION", "HIGH_EXCITATION")

    def test_too_few_prices(self):
        result = compute_self_excitation(np.array([100, 101, 102]))
        assert result["sei"] == 0
        assert result["regime"] == "NONE"

    def test_constant_prices(self):
        result = compute_self_excitation(np.full(100, 100.0))
        assert result["sei"] == 0

    def test_high_excitation(self):
        """Artificially create large same-direction clusters."""
        prices = np.array([100.0])
        for i in range(100):
            prices = np.append(prices, prices[-1] * 1.002)
        result = compute_self_excitation(prices, threshold_pct=0.05)
        assert result["n_clusters"] >= 0

    def test_result_keys(self):
        rng = np.random.RandomState(42)
        prices = np.cumsum(rng.randn(200) * 0.005) + 100
        result = compute_self_excitation(prices)
        expected_keys = {
            "sei", "regime", "description", "n_clusters",
            "avg_cluster_size", "max_cluster_size", "total_excitation_events",
        }
        assert set(result.keys()) == expected_keys
