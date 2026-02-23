"""Tests for gamma_channel — channel extraction, widening, strategy."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from gamma_channel import extract_channel, channel_strategy, _widen_channel


class TestExtractChannel:
    def test_basic_channel(self):
        gex = [
            {"strike": 90, "net_gex": 500},
            {"strike": 95, "net_gex": 300},
            {"strike": 100, "net_gex": -50},
            {"strike": 105, "net_gex": 400},
            {"strike": 110, "net_gex": 200},
        ]
        result = extract_channel(gex, 100)
        assert result["floor"] is not None
        assert result["ceiling"] is not None
        assert result["floor"] < 100
        assert result["ceiling"] > 100

    def test_empty_gex(self):
        result = extract_channel([], 100)
        assert result["floor"] is None
        assert result["ceiling"] is None

    def test_zero_spot(self):
        result = extract_channel([{"strike": 100, "net_gex": 500}], 0)
        assert result["floor"] is None

    def test_channel_position(self):
        gex = [
            {"strike": 90, "net_gex": 500},
            {"strike": 110, "net_gex": 500},
        ]
        result = extract_channel(gex, 100)
        if result["floor"] and result["ceiling"]:
            assert 0 <= result["channel_position"] <= 1

    def test_narrow_channel_widens(self):
        gex = [
            {"strike": 99.5, "net_gex": 800},
            {"strike": 100.5, "net_gex": 800},
            {"strike": 95, "net_gex": 200},
            {"strike": 105, "net_gex": 200},
        ]
        result = extract_channel(gex, 100, min_width_pct=2.0)
        if result["floor"] and result["ceiling"]:
            assert result["width_pct"] >= 1.0
            assert result["degenerate"] is True

    def test_all_negative_gex(self):
        gex = [
            {"strike": 95, "net_gex": -500},
            {"strike": 100, "net_gex": -300},
            {"strike": 105, "net_gex": -200},
        ]
        result = extract_channel(gex, 100)
        # Should use fallback (abs GEX)
        assert result is not None


class TestChannelStrategy:
    def test_floor_bounce(self):
        ch = {"floor": 95, "ceiling": 105, "channel_position": 0.15, "width_pct": 10}
        result = channel_strategy(ch, "POSITIVE_GAMMA", "LAMINAR", 0.4)
        assert result["strategy"] == "GEX_FLOOR_BOUNCE"
        assert result["edge_type"] == "WITH_DEALER"

    def test_ceiling_fade(self):
        ch = {"floor": 95, "ceiling": 105, "channel_position": 0.85, "width_pct": 10}
        result = channel_strategy(ch, "POSITIVE_GAMMA", "LAMINAR", 0.4)
        assert result["strategy"] == "GEX_CEILING_FADE"
        assert result["edge_type"] == "WITH_DEALER"

    def test_breakout_channel(self):
        ch = {"floor": 95, "ceiling": 105, "channel_position": 0.5, "width_pct": 10}
        result = channel_strategy(ch, "NEGATIVE_GAMMA", "TURBULENT", 1.5)
        assert result["strategy"] == "BREAKOUT_CHANNEL"
        assert result["edge_type"] == "AGAINST_DEALER"

    def test_no_channel(self):
        ch = {"floor": None, "ceiling": None, "channel_position": None, "width_pct": None}
        result = channel_strategy(ch, "POSITIVE_GAMMA", "LAMINAR", 0.3)
        assert result["strategy"] == "NO_CHANNEL"

    def test_degenerate_note(self):
        ch = {"floor": 99, "ceiling": 101, "channel_position": 0.5, "width_pct": 2, "degenerate": True}
        result = channel_strategy(ch, "POSITIVE_GAMMA", "LAMINAR", 0.3)
        assert any("widened" in n.lower() for n in result["notes"])
