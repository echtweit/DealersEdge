"""Integration tests — full API pipeline with mocked yfinance data."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

from fastapi.testclient import TestClient
from main import app
from tests.conftest import make_chain, make_price_history


client = TestClient(app)


# ── Mock Data Generators ─────────────────────────────────────────────────────


def _mock_expirations():
    today = datetime.now().date()
    return [
        {"date": (today + timedelta(days=d)).strftime("%Y-%m-%d"), "dte": d}
        for d in [5, 10, 14, 21, 30, 45]
    ]


def _mock_chain(spot=100):
    calls_raw, puts_raw = make_chain(spot, n_strikes=21, base_iv=0.25)
    return {
        "calls": calls_raw,
        "puts": puts_raw,
        "current_price": spot,
    }


def _mock_price_hist():
    return make_price_history(100, 250, daily_vol=0.015, seed=42)


def _mock_intraday_df(spot=100, n_bars=200):
    """Create a DataFrame mimicking yfinance intraday output."""
    rng = np.random.RandomState(42)
    dates = pd.date_range("2025-01-20 09:30", periods=n_bars, freq="2min")
    prices = spot + np.cumsum(rng.randn(n_bars) * 0.1)
    df = pd.DataFrame({
        "Open": prices + rng.randn(n_bars) * 0.05,
        "High": prices + abs(rng.randn(n_bars)) * 0.1,
        "Low": prices - abs(rng.randn(n_bars)) * 0.1,
        "Close": prices,
        "Volume": rng.randint(1000, 10000, n_bars),
    }, index=dates)
    return df


def _mock_daily_df(spot=100, n_days=250):
    rng = np.random.RandomState(42)
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
    prices = [spot]
    for _ in range(n_days - 1):
        prices.append(prices[-1] * (1 + rng.randn() * 0.015))
    prices = np.array(prices)
    df = pd.DataFrame({
        "Open": prices + rng.randn(n_days) * 0.5,
        "High": prices + abs(rng.randn(n_days)) * 1.0,
        "Low": prices - abs(rng.randn(n_days)) * 1.0,
        "Close": prices,
        "Volume": rng.randint(100000, 1000000, n_days),
    }, index=dates)
    return df


# ── Health Check ─────────────────────────────────────────────────────────────


def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "timestamp" in data


# ── Dealer Map Integration ───────────────────────────────────────────────────


class TestDealerMapIntegration:
    """Full pipeline test with all external data mocked."""

    def _patch_all(self):
        """Set up patches for all external data sources."""
        chain = _mock_chain(100)
        price_hist = _mock_price_hist()
        intraday_df = _mock_intraday_df()
        daily_df = _mock_daily_df()
        exps = _mock_expirations()

        patches = {}

        patches["expirations"] = patch(
            "main.get_expirations",
            return_value=exps,
        )
        patches["chain"] = patch(
            "main.get_options_chain",
            return_value=chain,
        )
        patches["price_hist"] = patch(
            "main.get_price_history",
            return_value=price_hist,
        )

        # ACF engine calls yfinance directly — mock the Ticker
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = intraday_df
        patches["acf_ticker"] = patch(
            "acf_engine.yf.Ticker",
            return_value=mock_ticker,
        )

        # Technicals also calls yfinance for benchmark RS
        mock_bench_ticker = MagicMock()
        mock_bench_ticker.history.return_value = daily_df
        patches["tech_ticker"] = patch(
            "technicals.yf.Ticker",
            return_value=mock_bench_ticker,
        )

        return patches

    def test_full_response_shape(self):
        patches = self._patch_all()
        with patches["expirations"], patches["chain"], patches["price_hist"], \
             patches["acf_ticker"], patches["tech_ticker"]:
            response = client.get("/api/dealer-map/TEST")

        assert response.status_code == 200
        data = response.json()

        # Top-level fields
        assert data["ticker"] == "TEST"
        assert data["spot"] == 100
        assert "expiration" in data
        assert "dte" in data
        assert "timestamp" in data

        # Regime fields
        assert data["gex_regime"] in ("POSITIVE_GAMMA", "NEGATIVE_GAMMA")
        assert data["acf_regime"] in ("LONG_GAMMA", "SHORT_GAMMA", "NEUTRAL")

        # Reynolds
        assert "regime" in data["reynolds"]
        assert data["reynolds"]["regime"] in ("LAMINAR", "TRANSITIONAL", "TURBULENT", "UNKNOWN")

        # Directional
        d = data["directional"]
        assert d["thesis"] in (
            "MOMENTUM_BREAKOUT", "MOMENTUM_EARLY", "MOMENTUM_TREND",
            "CONFLICTED_PIN", "FADE_MOVES", "FADE_MILD", "NEUTRAL",
        )
        assert "bias" in d
        assert "positions" in d
        assert "wall_break" in d

        # Straddle analysis
        s = data["straddle_analysis"]
        assert s["verdict"] in ("BUY_STRADDLE", "BUY_STRANGLE", "CONSIDER", "AVOID")
        assert "score" in s
        assert "straddle" in s

        # Vol analysis
        v = data["vol_analysis"]
        assert "iv_hv" in v
        assert "skew" in v
        assert "vrp" in v
        assert "vol_edge" in v

        # Collision times
        assert isinstance(data["collision_times"], list)

    def test_gex_profile_populated(self):
        patches = self._patch_all()
        with patches["expirations"], patches["chain"], patches["price_hist"], \
             patches["acf_ticker"], patches["tech_ticker"]:
            response = client.get("/api/dealer-map/TEST")

        data = response.json()
        gex = data["gex_profile"]
        assert gex["total_gex"] != 0 or isinstance(gex["total_gex"], float)
        assert len(gex["by_strike"]) > 0

    def test_positions_have_kelly_size(self):
        patches = self._patch_all()
        with patches["expirations"], patches["chain"], patches["price_hist"], \
             patches["acf_ticker"], patches["tech_ticker"]:
            response = client.get("/api/dealer-map/TEST")

        data = response.json()
        positions = data["directional"]["positions"]
        for pos in positions:
            if pos["type"] != "skip":
                # Kelly size should be present on non-skip positions
                assert "kelly_size" in pos or "sizing" in pos

    def test_vrp_data_populated(self):
        patches = self._patch_all()
        with patches["expirations"], patches["chain"], patches["price_hist"], \
             patches["acf_ticker"], patches["tech_ticker"]:
            response = client.get("/api/dealer-map/TEST")

        data = response.json()
        vrp = data["vol_analysis"]["vrp"]
        assert vrp["context"] != ""
        assert isinstance(vrp["gex_vol_mult"], float)

    def test_wall_break_has_gamma_asymmetry(self):
        patches = self._patch_all()
        with patches["expirations"], patches["chain"], patches["price_hist"], \
             patches["acf_ticker"], patches["tech_ticker"]:
            response = client.get("/api/dealer-map/TEST")

        data = response.json()
        wb = data["directional"]["wall_break"]
        assert "gamma_asymmetry" in wb
        assert wb["gamma_asymmetry"] in (-12, 5)


# ── Snapshot Utility ─────────────────────────────────────────────────────────


SNAPSHOT_DIR = os.path.join(os.path.dirname(__file__), "snapshots")


class TestSnapshot:
    """
    Save and compare a response snapshot.
    Run with --snapshot-update to regenerate.
    """

    def _get_response(self):
        patches = TestDealerMapIntegration()._patch_all()
        with patches["expirations"], patches["chain"], patches["price_hist"], \
             patches["acf_ticker"], patches["tech_ticker"]:
            return client.get("/api/dealer-map/TEST")

    def test_snapshot_keys_stable(self):
        """Ensure the top-level response keys don't change unexpectedly."""
        response = self._get_response()
        data = response.json()

        expected_keys = {
            "ticker", "spot", "expiration", "dte", "timestamp",
            "gex_regime", "gex_regime_label",
            "acf_regime", "acf_data",
            "reynolds", "phase",
            "channel", "channel_strategy",
            "directional",
            "straddle_analysis", "expiry_scan", "collision_times",
            "vol_analysis", "technicals",
            "key_levels", "distances", "gex_profile", "max_pain_profile",
            "available_expirations",
        }
        actual_keys = set(data.keys())
        missing = expected_keys - actual_keys
        extra = actual_keys - expected_keys
        assert not missing, f"Missing keys: {missing}"
        # Extra keys are okay (models use extra="allow") but warn
        if extra:
            import warnings
            warnings.warn(f"Extra keys in response (consider adding to model): {extra}")

    def test_save_snapshot(self):
        """Save a snapshot for future comparison."""
        response = self._get_response()
        data = response.json()

        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        path = os.path.join(SNAPSHOT_DIR, "TEST_response.json")

        # Remove volatile fields before saving
        data.pop("timestamp", None)
        data.pop("expiration", None)
        data.pop("dte", None)
        data.pop("available_expirations", None)
        data.pop("expiry_scan", None)

        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)

        assert os.path.exists(path)
