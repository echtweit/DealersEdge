"""
SQLite persistence for PaperTrader.
Tables: scans, trades, price_checks.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    scan_time       TEXT    NOT NULL,
    spot_price      REAL    NOT NULL,
    expiration      TEXT,
    dte             INTEGER,
    gex_regime      TEXT,
    acf_regime      TEXT,
    reynolds_number REAL,
    reynolds_regime TEXT,
    entropy_regime  TEXT,
    phase_regime    TEXT,
    thesis          TEXT,
    thesis_label    TEXT,
    straddle_verdict TEXT,
    straddle_score  REAL,
    full_response   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    ticker          TEXT    NOT NULL,
    trade_type      TEXT    NOT NULL,

    entry_time      TEXT    NOT NULL,
    entry_spot      REAL    NOT NULL,
    entry_premium   REAL,
    option_type     TEXT,
    strike          REAL,
    expiry_date     TEXT,
    dte_at_entry    INTEGER,
    contracts       INTEGER DEFAULT 1,

    confidence      TEXT,
    kelly_pct       REAL,
    risk_dollars    REAL,
    gex_regime      TEXT,
    reynolds_number REAL,
    reynolds_regime TEXT,
    acf_regime      TEXT,
    entropy_regime  TEXT,
    wall_break_prob REAL,
    thesis          TEXT,
    edge_type       TEXT,
    vrp_label       TEXT,
    iv_rv_ratio     REAL,
    atm_iv          REAL,

    target_price    REAL,
    stop_loss_pct   REAL    DEFAULT 50.0,
    max_hold_days   INTEGER,

    exit_time       TEXT,
    exit_spot       REAL,
    exit_premium    REAL,
    exit_reason     TEXT,

    pnl_dollars     REAL,
    pnl_pct         REAL,

    status          TEXT    DEFAULT 'OPEN',
    position_snapshot TEXT
);

CREATE TABLE IF NOT EXISTS price_checks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        INTEGER NOT NULL REFERENCES trades(id),
    check_time      TEXT    NOT NULL,
    spot_price      REAL    NOT NULL,
    option_mid      REAL,
    unrealized_pnl_pct REAL,
    iv_confirmation INTEGER
);

CREATE TABLE IF NOT EXISTS strike_iv_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    expiry_date     TEXT    NOT NULL,
    snapshot_time   TEXT    NOT NULL,
    tag             TEXT    NOT NULL,  -- CALL_WALL / PUT_WALL / ATM
    strike          REAL    NOT NULL,
    iv              REAL,
    bid             REAL,
    ask             REAL,
    open_interest   INTEGER,
    volume          INTEGER,
    spot_price      REAL
);

CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
CREATE INDEX IF NOT EXISTS idx_trades_thesis ON trades(thesis);
CREATE INDEX IF NOT EXISTS idx_trades_confidence ON trades(confidence);
CREATE INDEX IF NOT EXISTS idx_trades_gex_regime ON trades(gex_regime);
CREATE INDEX IF NOT EXISTS idx_trades_reynolds_regime ON trades(reynolds_regime);
CREATE INDEX IF NOT EXISTS idx_trades_acf_regime ON trades(acf_regime);
CREATE INDEX IF NOT EXISTS idx_trades_entropy_regime ON trades(entropy_regime);
CREATE INDEX IF NOT EXISTS idx_trades_edge_type ON trades(edge_type);
CREATE INDEX IF NOT EXISTS idx_trades_vrp_label ON trades(vrp_label);
CREATE INDEX IF NOT EXISTS idx_price_checks_trade ON price_checks(trade_id);
CREATE INDEX IF NOT EXISTS idx_iv_snaps_lookup ON strike_iv_snapshots(ticker, expiry_date, tag, snapshot_time);
"""


@contextmanager
def get_conn(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str = DB_PATH):
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)
        # Lightweight migrations for existing DBs.
        _ensure_column(conn, "price_checks", "iv_confirmation", "INTEGER")


def _ensure_column(conn, table: str, column: str, col_type: str):
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    names = {c["name"] for c in cols}
    if column not in names:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


# --------------- scans ---------------

def insert_scan(conn, ticker: str, spot: float, response: dict) -> int:
    directional = response.get("directional", {})
    straddle = response.get("straddle_analysis", {})
    reynolds = response.get("reynolds", {})
    acf = response.get("acf_data", {})
    phase = response.get("phase", {})
    gex_profile = response.get("gex_profile", {})
    entropy = gex_profile.get("entropy", {})

    cur = conn.execute(
        """INSERT INTO scans
           (ticker, scan_time, spot_price, expiration, dte,
            gex_regime, acf_regime, reynolds_number, reynolds_regime,
            entropy_regime, phase_regime,
            thesis, thesis_label, straddle_verdict, straddle_score,
            full_response)
           VALUES (?,?,?,?,?, ?,?,?,?, ?,?, ?,?,?,?, ?)""",
        (
            ticker,
            datetime.utcnow().isoformat(),
            spot,
            response.get("expiration"),
            response.get("dte"),
            response.get("gex_regime"),
            acf.get("stability"),
            reynolds.get("number"),
            reynolds.get("regime"),
            entropy.get("regime"),
            phase.get("regime"),
            directional.get("thesis"),
            directional.get("thesis_label"),
            straddle.get("verdict"),
            straddle.get("score", {}).get("total") if isinstance(straddle.get("score"), dict) else None,
            json.dumps(response),
        ),
    )
    return cur.lastrowid


# --------------- trades ---------------

def insert_trade(conn, scan_id: int, ticker: str, trade: dict) -> int:
    cur = conn.execute(
        """INSERT INTO trades
           (scan_id, ticker, trade_type,
            entry_time, entry_spot, entry_premium,
            option_type, strike, expiry_date, dte_at_entry, contracts,
            confidence, kelly_pct, risk_dollars,
            gex_regime, reynolds_number, reynolds_regime,
            acf_regime, entropy_regime,
            wall_break_prob, thesis, edge_type,
            vrp_label, iv_rv_ratio, atm_iv,
            target_price, stop_loss_pct, max_hold_days,
            status, position_snapshot)
           VALUES (?,?,?, ?,?,?, ?,?,?,?,?, ?,?,?, ?,?,?, ?,?, ?,?,?, ?,?,?, ?,?,?, ?,?)""",
        (
            scan_id,
            ticker,
            trade["trade_type"],
            datetime.utcnow().isoformat(),
            trade["entry_spot"],
            trade.get("entry_premium"),
            trade.get("option_type"),
            trade.get("strike"),
            trade.get("expiry_date"),
            trade.get("dte_at_entry"),
            trade.get("contracts", 1),
            trade.get("confidence"),
            trade.get("kelly_pct"),
            trade.get("risk_dollars"),
            trade.get("gex_regime"),
            trade.get("reynolds_number"),
            trade.get("reynolds_regime"),
            trade.get("acf_regime"),
            trade.get("entropy_regime"),
            trade.get("wall_break_prob"),
            trade.get("thesis"),
            trade.get("edge_type"),
            trade.get("vrp_label"),
            trade.get("iv_rv_ratio"),
            trade.get("atm_iv"),
            trade.get("target_price"),
            trade.get("stop_loss_pct", 50.0),
            trade.get("max_hold_days"),
            "OPEN",
            json.dumps(trade.get("position_snapshot", {})),
        ),
    )
    return cur.lastrowid


def get_open_trades(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM trades WHERE status = 'OPEN' ORDER BY entry_time"
    ).fetchall()
    return [dict(r) for r in rows]


def get_closed_trades(conn, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM trades WHERE status = 'CLOSED' ORDER BY exit_time DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_closed_trades(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM trades WHERE status = 'CLOSED' ORDER BY exit_time"
    ).fetchall()
    return [dict(r) for r in rows]


def close_trade(conn, trade_id: int, exit_spot: float,
                exit_premium: Optional[float], exit_reason: str,
                pnl_dollars: Optional[float], pnl_pct: Optional[float]):
    conn.execute(
        """UPDATE trades
           SET exit_time = ?, exit_spot = ?, exit_premium = ?,
               exit_reason = ?, pnl_dollars = ?, pnl_pct = ?,
               status = 'CLOSED'
           WHERE id = ?""",
        (
            datetime.utcnow().isoformat(),
            exit_spot,
            exit_premium,
            exit_reason,
            pnl_dollars,
            pnl_pct,
            trade_id,
        ),
    )


def trade_exists_for_scan(conn, scan_id: int, strike: float,
                          option_type: str) -> bool:
    """Prevent duplicate entries for the same signal."""
    row = conn.execute(
        """SELECT 1 FROM trades
           WHERE scan_id = ? AND strike = ? AND option_type = ?
           LIMIT 1""",
        (scan_id, strike, option_type),
    ).fetchone()
    return row is not None


def has_open_trade(conn, ticker: str, strike: float,
                   option_type: str, expiry_date: str) -> bool:
    """Check if we already have an open trade for this exact contract."""
    row = conn.execute(
        """SELECT 1 FROM trades
           WHERE ticker = ? AND strike = ? AND option_type = ?
                 AND expiry_date = ? AND status = 'OPEN'
           LIMIT 1""",
        (ticker, strike, option_type, expiry_date),
    ).fetchone()
    return row is not None


# --------------- price_checks ---------------

def insert_price_check(conn, trade_id: int, spot: float,
                       option_mid: Optional[float],
                       unrealized_pnl_pct: Optional[float],
                       iv_confirmation: Optional[int] = None):
    conn.execute(
        """INSERT INTO price_checks
           (trade_id, check_time, spot_price, option_mid, unrealized_pnl_pct, iv_confirmation)
           VALUES (?,?,?,?,?,?)""",
        (trade_id, datetime.utcnow().isoformat(), spot, option_mid,
         unrealized_pnl_pct, iv_confirmation),
    )


def get_price_history_for_trade(conn, trade_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM price_checks WHERE trade_id = ? ORDER BY check_time",
        (trade_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def insert_strike_iv_snapshot(conn, ticker: str, expiry_date: str, tag: str,
                              strike: float, iv: Optional[float], bid: Optional[float],
                              ask: Optional[float], open_interest: Optional[int],
                              volume: Optional[int], spot_price: float):
    conn.execute(
        """INSERT INTO strike_iv_snapshots
           (ticker, expiry_date, snapshot_time, tag, strike, iv, bid, ask, open_interest, volume, spot_price)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            ticker, expiry_date, datetime.utcnow().isoformat(), tag, strike, iv,
            bid, ask, open_interest, volume, spot_price,
        ),
    )


def get_latest_two_iv_snapshots(conn, ticker: str, expiry_date: str, tag: str) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM strike_iv_snapshots
           WHERE ticker = ? AND expiry_date = ? AND tag = ?
           ORDER BY id DESC
           LIMIT 2""",
        (ticker, expiry_date, tag),
    ).fetchall()
    return [dict(r) for r in rows]
