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

    def fake_quote(ticker, expiry, strike, side, chain=None):
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


def _directional_fixture(thesis="MOMENTUM_BREAKOUT", dte=1, confidence="MEDIUM", edge_type="WITH_DEALER"):
    pos = {
        "action": "BUY",
        "type": "early_momentum",
        "option_type": "CALL",
        "strike": 105.0,
        "target": "$110 call wall",
        "stop": "Cut at -50% of premium",
        "dte_guidance": "0-3 DTE",
        "confidence": confidence,
        "edge_type": edge_type,
    }
    response = {
        "ticker": "TEST",
        "expiration": "2026-02-27",
        "dte": dte,
        "directional": {"thesis": thesis, "atm_iv": 30.0, "wall_break": {"probability": 25}},
        "reynolds": {"number": 1.1, "regime": "TURBULENT"},
        "acf_data": {"stability": "STABLE"},
        "gex_profile": {"entropy": {"regime": "MODERATE"}},
        "vol_analysis": {"vrp": {"label": "FAIR"}, "iv_hv": {"iv_hv_ratio": 1.2}},
        "key_levels": {"call_wall": 110.0, "put_wall": 95.0, "max_pain": 102.0},
    }
    return pos, response


def test_challenger_filters_breakout_without_high_confidence(monkeypatch):
    monkeypatch.setattr(scanner.pricing, "get_option_mid", lambda *args, **kwargs: 1.0)
    monkeypatch.setattr(scanner, "CHALLENGER_V1_ENABLED", True)
    monkeypatch.setattr(scanner, "CHALLENGER_BREAKOUT_MIN_CONFIDENCE", "HIGH")
    monkeypatch.setattr(scanner, "CHALLENGER_MIN_DIRECTIONAL_DTE", 0)
    monkeypatch.setattr(scanner, "CHALLENGER_DTE_BYPASS_MIN_CONFIDENCE", "HIGH")
    monkeypatch.setattr(scanner, "CHALLENGER_UNCONFIRMED_STOP_PCT", 45.0)
    pos, response = _directional_fixture(thesis="MOMENTUM_BREAKOUT", dte=3, confidence="MEDIUM")
    trade = scanner._build_directional_trade(pos, response, 100.0)
    assert trade is None


def test_challenger_filters_low_dte_without_high_confidence(monkeypatch):
    monkeypatch.setattr(scanner.pricing, "get_option_mid", lambda *args, **kwargs: 1.0)
    monkeypatch.setattr(scanner, "CHALLENGER_V1_ENABLED", True)
    monkeypatch.setattr(scanner, "CHALLENGER_BREAKOUT_MIN_CONFIDENCE", "HIGH")
    monkeypatch.setattr(scanner, "CHALLENGER_MIN_DIRECTIONAL_DTE", 2)
    monkeypatch.setattr(scanner, "CHALLENGER_DTE_BYPASS_MIN_CONFIDENCE", "HIGH")
    monkeypatch.setattr(scanner, "CHALLENGER_UNCONFIRMED_STOP_PCT", 45.0)
    pos, response = _directional_fixture(thesis="MOMENTUM_EARLY", dte=1, confidence="MEDIUM")
    trade = scanner._build_directional_trade(pos, response, 100.0)
    assert trade is None


def test_challenger_tightens_stop_for_unconfirmed_proxy(monkeypatch):
    monkeypatch.setattr(scanner.pricing, "get_option_mid", lambda *args, **kwargs: 1.0)
    monkeypatch.setattr(scanner, "CHALLENGER_V1_ENABLED", True)
    monkeypatch.setattr(scanner, "CHALLENGER_BREAKOUT_MIN_CONFIDENCE", "HIGH")
    monkeypatch.setattr(scanner, "CHALLENGER_MIN_DIRECTIONAL_DTE", 0)
    monkeypatch.setattr(scanner, "CHALLENGER_DTE_BYPASS_MIN_CONFIDENCE", "HIGH")
    monkeypatch.setattr(scanner, "CHALLENGER_UNCONFIRMED_STOP_PCT", 45.0)
    pos, response = _directional_fixture(thesis="MOMENTUM_EARLY", dte=3, confidence="MEDIUM")
    trade = scanner._build_directional_trade(pos, response, 100.0)
    assert trade is not None
    assert trade["stop_loss_pct"] == 45.0


def test_challenger_v2_blocks_neutral_thesis(monkeypatch):
    monkeypatch.setattr(scanner.pricing, "get_option_mid", lambda *args, **kwargs: 1.0)
    monkeypatch.setattr(scanner, "CHALLENGER_V1_ENABLED", False)
    monkeypatch.setattr(scanner, "CHALLENGER_V2_ENABLED", True)
    monkeypatch.setattr(scanner, "CH_V2_BLOCK_THESES", "NEUTRAL")
    monkeypatch.setattr(scanner, "CH_V2_MIN_DIRECTIONAL_DTE", 0)
    monkeypatch.setattr(scanner, "CH_V2_DTE_BYPASS_MIN_CONFIDENCE", "HIGH")
    monkeypatch.setattr(scanner, "CH_V2_BREAKOUT_MIN_CONFIDENCE", "HIGH")
    monkeypatch.setattr(scanner, "CH_V2_BREAKOUT_REQUIRE_WITH_DEALER", True)
    monkeypatch.setattr(scanner, "CH_V2_UNCONFIRMED_STOP_PCT", 40.0)
    pos, response = _directional_fixture(thesis="NEUTRAL", dte=3, confidence="HIGH")
    trade = scanner._build_directional_trade(pos, response, 100.0)
    assert trade is None


def test_challenger_v2_breakout_requires_with_dealer(monkeypatch):
    monkeypatch.setattr(scanner.pricing, "get_option_mid", lambda *args, **kwargs: 1.0)
    monkeypatch.setattr(scanner, "CHALLENGER_V1_ENABLED", False)
    monkeypatch.setattr(scanner, "CHALLENGER_V2_ENABLED", True)
    monkeypatch.setattr(scanner, "CH_V2_BLOCK_THESES", "")
    monkeypatch.setattr(scanner, "CH_V2_MIN_DIRECTIONAL_DTE", 0)
    monkeypatch.setattr(scanner, "CH_V2_DTE_BYPASS_MIN_CONFIDENCE", "HIGH")
    monkeypatch.setattr(scanner, "CH_V2_BREAKOUT_MIN_CONFIDENCE", "HIGH")
    monkeypatch.setattr(scanner, "CH_V2_BREAKOUT_REQUIRE_WITH_DEALER", True)
    monkeypatch.setattr(scanner, "CH_V2_UNCONFIRMED_STOP_PCT", 40.0)
    pos, response = _directional_fixture(
        thesis="MOMENTUM_BREAKOUT", dte=3, confidence="HIGH", edge_type="COUNTER_DEALER"
    )
    trade = scanner._build_directional_trade(pos, response, 100.0)
    assert trade is None


def test_challenger_v2_tightens_stop_more_than_v1(monkeypatch):
    monkeypatch.setattr(scanner.pricing, "get_option_mid", lambda *args, **kwargs: 1.0)
    monkeypatch.setattr(scanner, "CHALLENGER_V1_ENABLED", False)
    monkeypatch.setattr(scanner, "CHALLENGER_V2_ENABLED", True)
    monkeypatch.setattr(scanner, "CH_V2_BLOCK_THESES", "")
    monkeypatch.setattr(scanner, "CH_V2_MIN_DIRECTIONAL_DTE", 0)
    monkeypatch.setattr(scanner, "CH_V2_DTE_BYPASS_MIN_CONFIDENCE", "HIGH")
    monkeypatch.setattr(scanner, "CH_V2_BREAKOUT_MIN_CONFIDENCE", "HIGH")
    monkeypatch.setattr(scanner, "CH_V2_BREAKOUT_REQUIRE_WITH_DEALER", True)
    monkeypatch.setattr(scanner, "CH_V2_UNCONFIRMED_STOP_PCT", 40.0)
    pos, response = _directional_fixture(thesis="MOMENTUM_EARLY", dte=3, confidence="MEDIUM")
    trade = scanner._build_directional_trade(pos, response, 100.0)
    assert trade is not None
    assert trade["stop_loss_pct"] == 40.0


def test_scan_ticker_links_trade_to_signal_id(monkeypatch):
    response = {
        "ticker": "TEST",
        "spot": 100.0,
        "directional": {"positions": [{"name": "x"}]},
        "straddle_analysis": {"verdict": "WAIT"},
    }

    signal_calls = []
    trade_calls = []
    ids = {"sig": 0}

    def fake_insert_signal(conn, scan_id, sig):
        ids["sig"] += 1
        signal_calls.append((scan_id, sig))
        return ids["sig"]

    def fake_insert_trade(conn, scan_id, ticker, trade, source_signal_id=None):
        trade_calls.append((scan_id, ticker, trade, source_signal_id))
        return 999

    monkeypatch.setattr(scanner, "fetch_dealer_map", lambda ticker, account_size=None: response)
    monkeypatch.setattr(scanner.db, "insert_scan", lambda conn, ticker, spot, resp: 42)
    monkeypatch.setattr(scanner, "_build_directional_signal", lambda pos, resp, spot: {"ticker": "TEST"})
    monkeypatch.setattr(scanner, "_build_directional_trade", lambda pos, resp, spot: {"strike": 100, "option_type": "CALL", "expiry_date": "2026-03-20"})
    monkeypatch.setattr(scanner, "_build_straddle_signal", lambda resp, spot: None)
    monkeypatch.setattr(scanner, "_build_straddle_trade", lambda resp, spot: None)
    monkeypatch.setattr(scanner.db, "insert_signal", fake_insert_signal)
    monkeypatch.setattr(scanner.db, "insert_trade", fake_insert_trade)
    monkeypatch.setattr(scanner.db, "has_open_trade", lambda *args, **kwargs: False)

    out = scanner.scan_ticker("TEST", conn=object(), account_size=None)
    assert out == [999]
    assert len(signal_calls) == 1
    assert len(trade_calls) == 1
    assert trade_calls[0][3] == 1
