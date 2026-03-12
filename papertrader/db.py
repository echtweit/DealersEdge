"""
SQLite persistence for PaperTrader.
Tables: scans, trades, price_checks.
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
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
    weekday         INTEGER,
    is_expiry_day   INTEGER DEFAULT 0,
    is_monthly_opex INTEGER DEFAULT 0,
    is_quarterly_opex INTEGER DEFAULT 0,
    days_to_opex    INTEGER,
    max_pain_level  REAL,
    dist_to_max_pain_abs REAL,
    dist_to_max_pain_pct REAL,
    mkt_vix         REAL,
    mkt_vvix        REAL,
    mkt_tnx         REAL,
    mkt_dxy         REAL,
    mkt_spy         REAL,
    mkt_qqq         REAL,
    mkt_iwm         REAL,
    mkt_spy_ret_1d_pct REAL,
    mkt_qqq_ret_1d_pct REAL,
    mkt_iwm_ret_1d_pct REAL,
    market_context_json TEXT,
    full_response   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    source_signal_id INTEGER REFERENCES signals(id),
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
    min_dist_to_max_pain_pct REAL,
    hit_max_pain_flag INTEGER,
    dist_compression_pct REAL,

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
    iv_confirmation INTEGER,
    max_pain_level  REAL,
    dist_to_max_pain_abs REAL,
    dist_to_max_pain_pct REAL,
    pricing_degraded_flag INTEGER DEFAULT 0
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

CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    ticker          TEXT    NOT NULL,
    signal_kind     TEXT    NOT NULL,  -- DIRECTIONAL / STRADDLE / STRANGLE
    direction       TEXT    NOT NULL,  -- UP / DOWN / VOL
    option_type     TEXT,
    strike          REAL,
    target_price    REAL,
    entry_spot      REAL    NOT NULL,
    entry_time      TEXT    NOT NULL,
    expiry_date     TEXT,
    dte_at_entry    INTEGER,
    thesis          TEXT,
    edge_type       TEXT,
    confidence      TEXT,
    metadata_json   TEXT,
    h1_move_pct     REAL,
    h3_move_pct     REAL,
    h5_move_pct     REAL,
    h1_hit          INTEGER,           -- 1=true, 0=false, null=not evaluable yet
    h3_hit          INTEGER,
    h5_hit          INTEGER,
    status          TEXT    DEFAULT 'PENDING',
    last_eval_time  TEXT
);

CREATE TABLE IF NOT EXISTS no_trade_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER REFERENCES scans(id),
    ticker          TEXT    NOT NULL,
    event_time      TEXT    NOT NULL,
    candidate_kind  TEXT    NOT NULL,  -- DIRECTIONAL / STRADDLE / TICKER
    reason_code     TEXT    NOT NULL,
    trade_type      TEXT,
    option_type     TEXT,
    strike          REAL,
    confidence      TEXT,
    thesis          TEXT,
    edge_type       TEXT,
    reynolds_number REAL,
    reynolds_regime TEXT,
    dte             INTEGER,
    metadata_json   TEXT
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
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker);
CREATE INDEX IF NOT EXISTS idx_signals_thesis ON signals(thesis);
CREATE INDEX IF NOT EXISTS idx_no_trade_ticker_time ON no_trade_events(ticker, event_time);
CREATE INDEX IF NOT EXISTS idx_no_trade_reason ON no_trade_events(reason_code);
"""


@contextmanager
def get_conn(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _execute_write(conn, sql: str, params: tuple = (), retries: int = 4, backoff_s: float = 0.15):
    """
    Execute write statements with short retry on transient SQLite lock errors.
    """
    for i in range(retries + 1):
        try:
            return conn.execute(sql, params)
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            is_lock = "database is locked" in msg or "database table is locked" in msg
            if not is_lock or i >= retries:
                raise
            time.sleep(backoff_s * (i + 1))


def init_db(db_path: str = DB_PATH):
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)
        # Lightweight migrations for existing DBs.
        _ensure_column(conn, "price_checks", "iv_confirmation", "INTEGER")
        _ensure_column(conn, "scans", "weekday", "INTEGER")
        _ensure_column(conn, "scans", "is_expiry_day", "INTEGER")
        _ensure_column(conn, "scans", "is_monthly_opex", "INTEGER")
        _ensure_column(conn, "scans", "is_quarterly_opex", "INTEGER")
        _ensure_column(conn, "scans", "days_to_opex", "INTEGER")
        _ensure_column(conn, "scans", "max_pain_level", "REAL")
        _ensure_column(conn, "scans", "dist_to_max_pain_abs", "REAL")
        _ensure_column(conn, "scans", "dist_to_max_pain_pct", "REAL")
        _ensure_column(conn, "scans", "mkt_vix", "REAL")
        _ensure_column(conn, "scans", "mkt_vvix", "REAL")
        _ensure_column(conn, "scans", "mkt_tnx", "REAL")
        _ensure_column(conn, "scans", "mkt_dxy", "REAL")
        _ensure_column(conn, "scans", "mkt_spy", "REAL")
        _ensure_column(conn, "scans", "mkt_qqq", "REAL")
        _ensure_column(conn, "scans", "mkt_iwm", "REAL")
        _ensure_column(conn, "scans", "mkt_spy_ret_1d_pct", "REAL")
        _ensure_column(conn, "scans", "mkt_qqq_ret_1d_pct", "REAL")
        _ensure_column(conn, "scans", "mkt_iwm_ret_1d_pct", "REAL")
        _ensure_column(conn, "scans", "market_context_json", "TEXT")
        _ensure_column(conn, "price_checks", "max_pain_level", "REAL")
        _ensure_column(conn, "price_checks", "dist_to_max_pain_abs", "REAL")
        _ensure_column(conn, "price_checks", "dist_to_max_pain_pct", "REAL")
        _ensure_column(conn, "price_checks", "pricing_degraded_flag", "INTEGER")
        _ensure_column(conn, "trades", "min_dist_to_max_pain_pct", "REAL")
        _ensure_column(conn, "trades", "hit_max_pain_flag", "INTEGER")
        _ensure_column(conn, "trades", "dist_compression_pct", "REAL")
        _ensure_column(conn, "trades", "source_signal_id", "INTEGER")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_source_signal ON trades(source_signal_id)")


def _ensure_column(conn, table: str, column: str, col_type: str):
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    names = {c["name"] for c in cols}
    if column not in names:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


# --------------- scans ---------------

def _third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    while d.weekday() != 4:  # Friday
        d += timedelta(days=1)
    return d + timedelta(days=14)


def _is_monthly_opex(d: date) -> bool:
    return d.weekday() == 4 and d.day >= 15 and d.day <= 21


def _next_monthly_opex(from_date: date) -> date:
    this_month = _third_friday(from_date.year, from_date.month)
    if from_date <= this_month:
        return this_month
    if from_date.month == 12:
        return _third_friday(from_date.year + 1, 1)
    return _third_friday(from_date.year, from_date.month + 1)


def _extract_max_pain(response: dict) -> Optional[float]:
    kl = response.get("key_levels", {})
    level = kl.get("max_pain")
    if isinstance(level, (int, float)):
        return float(level)
    if isinstance(level, dict):
        strike = level.get("strike")
        if isinstance(strike, (int, float)):
            return float(strike)
    mp = response.get("max_pain_profile", {}).get("max_pain")
    if isinstance(mp, (int, float)):
        return float(mp)
    return None


def insert_scan(conn, ticker: str, spot: float, response: dict, market_context: Optional[dict] = None) -> int:
    directional = response.get("directional", {})
    straddle = response.get("straddle_analysis", {})
    reynolds = response.get("reynolds", {})
    acf = response.get("acf_data", {})
    phase = response.get("phase", {})
    gex_profile = response.get("gex_profile", {})
    entropy = gex_profile.get("entropy", {})
    now = datetime.utcnow()

    exp_date = None
    exp_str = response.get("expiration")
    if exp_str:
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        except ValueError:
            exp_date = None

    max_pain = _extract_max_pain(response)
    dist_abs = None
    dist_pct = None
    if max_pain is not None and isinstance(spot, (int, float)) and spot > 0:
        dist_abs = round(abs(float(spot) - max_pain), 4)
        dist_pct = round(dist_abs / float(spot) * 100, 4)

    is_monthly_opex = int(_is_monthly_opex(exp_date)) if exp_date else 0
    is_quarterly_opex = int(bool(is_monthly_opex and exp_date and exp_date.month in {3, 6, 9, 12}))
    days_to_opex = (_next_monthly_opex(now.date()) - now.date()).days
    mctx = market_context or {}

    cur = _execute_write(
        conn,
        """INSERT INTO scans
           (ticker, scan_time, spot_price, expiration, dte,
            gex_regime, acf_regime, reynolds_number, reynolds_regime,
            entropy_regime, phase_regime,
            thesis, thesis_label, straddle_verdict, straddle_score,
            weekday, is_expiry_day, is_monthly_opex, is_quarterly_opex,
            days_to_opex, max_pain_level, dist_to_max_pain_abs, dist_to_max_pain_pct,
            mkt_vix, mkt_vvix, mkt_tnx, mkt_dxy, mkt_spy, mkt_qqq, mkt_iwm,
            mkt_spy_ret_1d_pct, mkt_qqq_ret_1d_pct, mkt_iwm_ret_1d_pct, market_context_json,
            full_response)
           VALUES (?,?,?,?,?, ?,?,?,?, ?,?, ?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?,?, ?,?,?,?, ?,?,?,?, ?)""",
        (
            ticker,
            now.isoformat(),
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
            now.weekday(),
            int(bool(exp_date and exp_date == now.date())),
            is_monthly_opex,
            is_quarterly_opex,
            days_to_opex,
            max_pain,
            dist_abs,
            dist_pct,
            mctx.get("vix"),
            mctx.get("vvix"),
            mctx.get("tnx"),
            mctx.get("dxy"),
            mctx.get("spy"),
            mctx.get("qqq"),
            mctx.get("iwm"),
            mctx.get("spy_ret_1d_pct"),
            mctx.get("qqq_ret_1d_pct"),
            mctx.get("iwm_ret_1d_pct"),
            json.dumps(mctx),
            json.dumps(response),
        ),
    )
    return cur.lastrowid


# --------------- trades ---------------

def insert_trade(conn, scan_id: int, ticker: str, trade: dict, source_signal_id: Optional[int] = None) -> int:
    cur = _execute_write(
        conn,
        """INSERT INTO trades
           (scan_id, source_signal_id, ticker, trade_type,
            entry_time, entry_spot, entry_premium,
            option_type, strike, expiry_date, dte_at_entry, contracts,
            confidence, kelly_pct, risk_dollars,
            gex_regime, reynolds_number, reynolds_regime,
            acf_regime, entropy_regime,
            wall_break_prob, thesis, edge_type,
            vrp_label, iv_rv_ratio, atm_iv,
            target_price, stop_loss_pct, max_hold_days,
            status, position_snapshot)
           VALUES (?,?,?, ?,?,?, ?,?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?, ?,?,?, ?,?,?, ?,?,?, ?)""",
        (
            scan_id,
            source_signal_id,
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


def get_linked_signal_trade_rows(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            s.id AS signal_id,
            s.thesis AS signal_thesis,
            s.direction AS signal_direction,
            s.h1_hit,
            s.h3_hit,
            s.h5_hit,
            t.id AS trade_id,
            t.thesis AS trade_thesis,
            t.exit_reason,
            t.pnl_pct
        FROM trades t
        JOIN signals s ON s.id = t.source_signal_id
        WHERE t.status = 'CLOSED'
        """
    ).fetchall()
    return [dict(r) for r in rows]


def close_trade(conn, trade_id: int, exit_spot: float,
                exit_premium: Optional[float], exit_reason: str,
                pnl_dollars: Optional[float], pnl_pct: Optional[float],
                min_dist_to_max_pain_pct: Optional[float] = None,
                hit_max_pain_flag: Optional[int] = None,
                dist_compression_pct: Optional[float] = None):
    _execute_write(
        conn,
        """UPDATE trades
           SET exit_time = ?, exit_spot = ?, exit_premium = ?,
               exit_reason = ?, pnl_dollars = ?, pnl_pct = ?,
               min_dist_to_max_pain_pct = ?, hit_max_pain_flag = ?, dist_compression_pct = ?,
               status = 'CLOSED'
           WHERE id = ?""",
        (
            datetime.utcnow().isoformat(),
            exit_spot,
            exit_premium,
            exit_reason,
            pnl_dollars,
            pnl_pct,
            min_dist_to_max_pain_pct,
            hit_max_pain_flag,
            dist_compression_pct,
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
                       iv_confirmation: Optional[int] = None,
                       max_pain_level: Optional[float] = None,
                       dist_to_max_pain_abs: Optional[float] = None,
                       dist_to_max_pain_pct: Optional[float] = None,
                       pricing_degraded_flag: int = 0):
    _execute_write(
        conn,
        """INSERT INTO price_checks
           (trade_id, check_time, spot_price, option_mid, unrealized_pnl_pct, iv_confirmation,
            max_pain_level, dist_to_max_pain_abs, dist_to_max_pain_pct, pricing_degraded_flag)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (trade_id, datetime.utcnow().isoformat(), spot, option_mid,
         unrealized_pnl_pct, iv_confirmation, max_pain_level,
         dist_to_max_pain_abs, dist_to_max_pain_pct, int(bool(pricing_degraded_flag))),
    )


def get_price_history_for_trade(conn, trade_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM price_checks WHERE trade_id = ? ORDER BY check_time",
        (trade_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_latest_iv_confirmation(conn, trade_id: int) -> Optional[int]:
    row = conn.execute(
        """SELECT iv_confirmation
           FROM price_checks
           WHERE trade_id = ?
           ORDER BY id DESC
           LIMIT 1""",
        (trade_id,),
    ).fetchone()
    if not row:
        return None
    return row["iv_confirmation"]


def get_min_dist_to_max_pain_pct(conn, trade_id: int) -> Optional[float]:
    row = conn.execute(
        """SELECT MIN(dist_to_max_pain_pct) AS min_dist
           FROM price_checks
           WHERE trade_id = ? AND dist_to_max_pain_pct IS NOT NULL""",
        (trade_id,),
    ).fetchone()
    if not row:
        return None
    return row["min_dist"]


def insert_strike_iv_snapshot(conn, ticker: str, expiry_date: str, tag: str,
                              strike: float, iv: Optional[float], bid: Optional[float],
                              ask: Optional[float], open_interest: Optional[int],
                              volume: Optional[int], spot_price: float):
    _execute_write(
        conn,
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


# --------------- signals (forecast-quality layer) ---------------

def insert_signal(conn, scan_id: int, signal: dict) -> int:
    cur = _execute_write(
        conn,
        """INSERT INTO signals
           (scan_id, ticker, signal_kind, direction, option_type, strike,
            target_price, entry_spot, entry_time, expiry_date, dte_at_entry,
            thesis, edge_type, confidence, metadata_json)
           VALUES (?,?,?,?,?, ?,?,?,?, ?,?, ?,?,?,?)""",
        (
            scan_id,
            signal["ticker"],
            signal["signal_kind"],
            signal["direction"],
            signal.get("option_type"),
            signal.get("strike"),
            signal.get("target_price"),
            signal["entry_spot"],
            signal.get("entry_time") or datetime.utcnow().isoformat(),
            signal.get("expiry_date"),
            signal.get("dte_at_entry"),
            signal.get("thesis"),
            signal.get("edge_type"),
            signal.get("confidence"),
            json.dumps(signal.get("metadata", {})),
        ),
    )
    return cur.lastrowid


def insert_no_trade_event(conn, event: dict) -> int:
    cur = _execute_write(
        conn,
        """INSERT INTO no_trade_events
           (scan_id, ticker, event_time, candidate_kind, reason_code,
            trade_type, option_type, strike, confidence, thesis, edge_type,
            reynolds_number, reynolds_regime, dte, metadata_json)
           VALUES (?,?,?,?,?, ?,?,?,?, ?,?, ?,?,?,?)""",
        (
            event.get("scan_id"),
            event.get("ticker"),
            event.get("event_time") or datetime.utcnow().isoformat(),
            event.get("candidate_kind"),
            event.get("reason_code"),
            event.get("trade_type"),
            event.get("option_type"),
            event.get("strike"),
            event.get("confidence"),
            event.get("thesis"),
            event.get("edge_type"),
            event.get("reynolds_number"),
            event.get("reynolds_regime"),
            event.get("dte"),
            json.dumps(event.get("metadata", {})),
        ),
    )
    return cur.lastrowid


def get_signals_for_evaluation(conn, limit: int = 500) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM signals
           WHERE status IN ('PENDING', 'PARTIAL')
           ORDER BY id
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def update_signal_evaluation(conn, signal_id: int, updates: dict):
    set_parts = []
    vals = []
    for k, v in updates.items():
        set_parts.append(f"{k} = ?")
        vals.append(v)
    set_parts.append("last_eval_time = ?")
    vals.append(datetime.utcnow().isoformat())
    vals.append(signal_id)
    _execute_write(
        conn,
        f"UPDATE signals SET {', '.join(set_parts)} WHERE id = ?",
        tuple(vals),
    )


def get_signals(conn, status: Optional[str] = None) -> list[dict]:
    if status:
        rows = conn.execute(
            "SELECT * FROM signals WHERE status = ? ORDER BY id",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM signals ORDER BY id").fetchall()
    return [dict(r) for r in rows]
