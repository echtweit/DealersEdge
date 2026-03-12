"""Tests for signal-layer evaluator logic."""

import os
import sys
from contextlib import contextmanager

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from papertrader import signal_evaluator as se


def test_evaluate_signal_against_history_up_direction_status_transitions():
    signal = {
        "entry_time": "2026-02-20T12:00:00",
        "entry_spot": 100.0,
        "direction": "UP",
        "target_price": 102.0,
        "h5_move_pct": None,
    }
    closes = [
        {"date": "2026-02-20", "close": 100.0},
        {"date": "2026-02-23", "close": 101.0},
        {"date": "2026-02-24", "close": 103.0},
        {"date": "2026-02-25", "close": 104.0},
        {"date": "2026-02-26", "close": 105.0},
        {"date": "2026-02-27", "close": 106.0},
    ]
    updates = se._evaluate_signal_against_history(signal, closes)
    assert updates["h1_hit"] == 0
    assert updates["h3_hit"] == 1
    assert updates["h5_hit"] == 1
    assert updates["status"] == "EVALUATED"


def test_signal_hit_for_vol_uses_required_move():
    signal = {"direction": "VOL", "metadata_json": '{"required_move_pct": 3.5}'}
    assert se._signal_hit(signal, close_px=100.0, move_pct=3.0) == 0
    assert se._signal_hit(signal, close_px=100.0, move_pct=3.6) == 1


def test_evaluate_pending_signals_updates_rows(monkeypatch):
    fake_signals = [
        {
            "id": 1,
            "ticker": "TEST",
            "entry_time": "2026-02-20T12:00:00",
            "entry_spot": 100.0,
            "direction": "UP",
            "target_price": 101.0,
            "h5_move_pct": None,
        }
    ]
    closes = [
        {"date": "2026-02-20", "close": 100.0},
        {"date": "2026-02-21", "close": 102.0},
        {"date": "2026-02-22", "close": 103.0},
        {"date": "2026-02-23", "close": 104.0},
        {"date": "2026-02-24", "close": 105.0},
        {"date": "2026-02-25", "close": 106.0},
    ]

    updates = []

    @contextmanager
    def fake_conn():
        yield object()

    monkeypatch.setattr(se.db, "init_db", lambda: None)
    monkeypatch.setattr(se.db, "get_conn", lambda: fake_conn())
    monkeypatch.setattr(se.db, "get_signals_for_evaluation", lambda conn, limit=500: fake_signals)
    monkeypatch.setattr(se.db, "update_signal_evaluation", lambda conn, sid, upd: updates.append((sid, upd)))
    monkeypatch.setattr(se.pricing, "get_daily_closes", lambda ticker, period="2mo": closes)

    out = se.evaluate_pending_signals(limit=100)
    assert out["processed"] == 1
    assert out["updated"] == 1
    assert updates and updates[0][0] == 1


def test_signal_execution_bridge_report_renders(monkeypatch):
    @contextmanager
    def fake_conn():
        yield object()

    monkeypatch.setattr(se.db, "init_db", lambda: None)
    monkeypatch.setattr(se.db, "get_conn", lambda: fake_conn())
    monkeypatch.setattr(
        se.db,
        "get_signals",
        lambda conn: [
            {"thesis": "MOMENTUM_EARLY", "h3_hit": 1, "h5_hit": 0},
            {"thesis": "MOMENTUM_EARLY", "h3_hit": 1, "h5_hit": 1},
        ],
    )
    monkeypatch.setattr(
        se.db,
        "get_all_closed_trades",
        lambda conn: [
            {"thesis": "MOMENTUM_EARLY", "pnl_pct": 10.0},
            {"thesis": "MOMENTUM_EARLY", "pnl_pct": -5.0},
        ],
    )
    monkeypatch.setattr(
        se.db,
        "get_linked_signal_trade_rows",
        lambda conn: [
            {"h3_hit": 1, "h5_hit": 0, "pnl_pct": 10.0},
            {"h3_hit": 0, "h5_hit": 1, "pnl_pct": -5.0},
        ],
    )

    txt = se.signal_execution_bridge_report()
    assert "SIGNAL VS EXECUTION BRIDGE" in txt
    assert "MOMENTUM_EARLY" in txt
    assert "Exact signal->trade linkage" in txt
