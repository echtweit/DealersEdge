"""Tests for papertrader IV-confirmation plumbing."""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from papertrader import monitor, scanner


def test_scanner_normalizes_key_levels_to_numeric():
    pos = {
        "action": "BUY",
        "type": "early_momentum",
        "option_type": "CALL",
        "strike": 105.0,
        "target": "$110 call wall",
        "stop": "Cut at -50% of premium",
        "dte_guidance": "5-10 DTE",
        "confidence": "HIGH",
        "edge_type": "WITH_DEALER",
    }
    response = {
        "ticker": "TEST",
        "expiration": "2026-02-27",
        "dte": 7,
        "directional": {"thesis": "MOMENTUM_EARLY", "atm_iv": 30.0, "wall_break": {"probability": 25}},
        "reynolds": {"number": 1.1, "regime": "TURBULENT"},
        "acf_data": {"stability": "STABLE"},
        "gex_profile": {"entropy": {"regime": "MODERATE"}},
        "vol_analysis": {"vrp": {"label": "FAIR"}, "iv_hv": {"iv_hv_ratio": 1.2}},
        "key_levels": {
            "call_wall": {"strike": 110.0, "oi": 1000},
            "put_wall": {"strike": 95.0, "oi": 800},
            "max_pain": 102.0,
        },
    }

    trade = scanner._build_directional_trade(pos, response, 100.0)
    assert trade is not None
    assert trade["position_snapshot"]["key_levels"]["call_wall"] == 110.0
    assert trade["position_snapshot"]["key_levels"]["put_wall"] == 95.0
    assert trade["position_snapshot"]["key_levels"]["max_pain"] == 102.0


def test_monitor_level_strike_handles_numeric_and_dict():
    assert monitor._level_strike(123.0) == 123.0
    assert monitor._level_strike({"strike": 456.0, "oi": 100}) == 456.0
    assert monitor._level_strike({"oi": 100}) is None


def test_trade_has_wall_levels_for_dict_or_numeric():
    t_dict = {"position_snapshot": {"key_levels": {"call_wall": {"strike": 101.0}, "put_wall": None}}}
    t_num = {"position_snapshot": {"key_levels": {"call_wall": 101.0, "put_wall": None}}}
    t_none = {"position_snapshot": {"key_levels": {"call_wall": None, "put_wall": None}}}
    assert monitor._trade_has_wall_levels(t_dict) is True
    assert monitor._trade_has_wall_levels(t_num) is True
    assert monitor._trade_has_wall_levels(t_none) is False


def test_snapshot_key_strike_iv_collects_wall_and_atm_tags(monkeypatch):
    inserted_tags = []

    def fake_quote(ticker, expiry, strike, side):
        return {
            "strike": strike,
            "iv": 0.25,
            "bid": 1.0,
            "ask": 1.2,
            "open_interest": 100,
            "volume": 50,
        }

    def fake_insert(conn, ticker, expiry_date, tag, strike, iv, bid, ask, open_interest, volume, spot_price):
        inserted_tags.append(tag)

    monkeypatch.setattr(monitor.pricing, "get_strike_quote", fake_quote)
    monkeypatch.setattr(monitor.db, "insert_strike_iv_snapshot", fake_insert)

    trade = {
        "ticker": "TEST",
        "expiry_date": "2026-02-27",
        "position_snapshot": {
            "key_levels": {
                "call_wall": {"strike": 110.0, "oi": 1000},
                "put_wall": {"strike": 95.0, "oi": 800},
            }
        },
    }

    monitor._snapshot_key_strike_iv(conn=None, trade=trade, spot=100.0)
    assert "CALL_WALL" in inserted_tags
    assert "PUT_WALL" in inserted_tags
    assert "ATM" in inserted_tags


def test_iv_confirmation_for_straddle_uses_wall_deltas(monkeypatch):
    def fake_latest_two(conn, ticker, expiry, tag):
        if tag == "CALL_WALL":
            return [{"iv": 0.30}, {"iv": 0.28}]
        if tag == "PUT_WALL":
            return [{"iv": 0.32}, {"iv": 0.30}]
        return []

    monkeypatch.setattr(monitor.db, "get_latest_two_iv_snapshots", fake_latest_two)
    trade = {"ticker": "TEST", "expiry_date": "2026-02-27", "option_type": "STRADDLE"}
    assert monitor._compute_iv_confirmation(conn=None, trade=trade) == 1
