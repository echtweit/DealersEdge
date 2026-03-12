"""
Monitor: checks open trades for exit conditions and closes them.
Exit triggers: target hit, stop hit, expiry, max hold time exceeded.
"""
import json
import logging
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from . import db, pricing
from .config import (
    EXIT_ON_SIGNAL_FLIP,
    EXIT_ON_REGIME_BREAK,
    EXIT_ON_IV_CONFIRMATION_LOSS,
)
from .scanner import fetch_dealer_map

log = logging.getLogger(__name__)


def _days_held(entry_time_str: str) -> int:
    entry = datetime.fromisoformat(entry_time_str)
    return (datetime.utcnow() - entry).days


def _get_current_premium(trade: dict, spot: float, chain=None) -> Optional[float]:
    """Fetch live option mid-price for the trade's contract."""
    ticker = trade["ticker"]
    expiry = trade.get("expiry_date")
    strike = trade.get("strike", 0)
    opt_type = trade.get("option_type", "")

    if not expiry or not strike:
        return None

    if opt_type == "STRADDLE":
        return pricing.get_straddle_mid(ticker, expiry, strike, chain=chain)
    elif opt_type == "STRANGLE":
        snapshot = trade.get("position_snapshot")
        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)
        strangle = (snapshot or {}).get("strangle", {})
        cs = strangle.get("call_strike", strike + 5)
        ps = strangle.get("put_strike", strike - 5)
        return pricing.get_strangle_mid(ticker, expiry, cs, ps, chain=chain)
    elif opt_type in ("CALL", "PUT"):
        return pricing.get_option_mid(ticker, expiry, strike, opt_type, chain=chain)
    return None


def _compute_pnl(entry_premium: Optional[float],
                 exit_premium: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    """Returns (pnl_dollars_per_share, pnl_pct)."""
    if entry_premium and exit_premium and entry_premium > 0:
        pnl = round(exit_premium - entry_premium, 4)
        pnl_pct = round(pnl / entry_premium * 100, 2)
        return pnl * 100, pnl_pct  # ×100 for per-contract
    return None, None


def _check_target(trade: dict, spot: float) -> bool:
    target = trade.get("target_price")
    if target is None:
        return False

    opt_type = trade.get("option_type", "")
    if opt_type == "CALL":
        return spot >= target
    elif opt_type == "PUT":
        return spot <= target
    elif opt_type in ("STRADDLE", "STRANGLE"):
        snapshot = trade.get("position_snapshot")
        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)
        lower_be = (snapshot or {}).get("lower_breakeven")
        upper_be = target
        if upper_be and spot >= upper_be:
            return True
        if lower_be and spot <= lower_be:
            return True
    return False


def _check_stop(trade: dict, current_premium: Optional[float]) -> bool:
    entry_premium = trade.get("entry_premium")
    stop_pct = trade.get("stop_loss_pct", 50.0)
    if not entry_premium or not current_premium or entry_premium <= 0:
        return False
    loss_pct = (entry_premium - current_premium) / entry_premium * 100
    return loss_pct >= stop_pct


def _check_expiry(trade: dict) -> bool:
    expiry_str = trade.get("expiry_date")
    if not expiry_str:
        return False
    try:
        exp_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
        now_et = datetime.now(ZoneInfo("America/New_York"))
        expiry_cutoff_et = datetime.combine(
            exp_date, time(hour=16, minute=0), tzinfo=ZoneInfo("America/New_York")
        )
        # Treat expiry as effective after regular market close on expiry day.
        return now_et >= expiry_cutoff_et
    except (ValueError, TypeError):
        return False


def _check_time_limit(trade: dict) -> bool:
    max_days = trade.get("max_hold_days")
    if not max_days:
        return False
    return _days_held(trade["entry_time"]) >= max_days


def _level_strike(level) -> Optional[float]:
    """Handle key levels stored as numeric or {'strike': ...} dict."""
    if isinstance(level, (int, float)):
        return float(level)
    if isinstance(level, dict):
        strike = level.get("strike")
        if isinstance(strike, (int, float)):
            return float(strike)
    return None


def _snapshot_key_strike_iv(conn, trade: dict, spot: float, chain=None):
    """Capture CALL_WALL / PUT_WALL / ATM strike IV snapshots for this ticker+expiry."""
    snapshot = trade.get("position_snapshot")
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    snapshot = snapshot or {}

    key_levels = snapshot.get("key_levels", {})
    call_wall = _level_strike(key_levels.get("call_wall"))
    put_wall = _level_strike(key_levels.get("put_wall"))
    expiry = trade.get("expiry_date")
    ticker = trade.get("ticker")

    if not ticker or not expiry:
        return

    points = []
    if isinstance(call_wall, (int, float)) and call_wall > 0:
        points.append(("CALL_WALL", float(call_wall), "CALL"))
    if isinstance(put_wall, (int, float)) and put_wall > 0:
        points.append(("PUT_WALL", float(put_wall), "PUT"))

    # ATM proxy from spot, on call side for consistency.
    points.append(("ATM", float(spot), "CALL"))

    for tag, strike, side in points:
        q = pricing.get_strike_quote(ticker, expiry, strike, side, chain=chain)
        if not q:
            continue
        db.insert_strike_iv_snapshot(
            conn,
            ticker=ticker,
            expiry_date=expiry,
            tag=tag,
            strike=q["strike"],
            iv=q["iv"],
            bid=q["bid"],
            ask=q["ask"],
            open_interest=q["open_interest"],
            volume=q["volume"],
            spot_price=spot,
        )


def _iv_delta(conn, ticker: str, expiry: str, tag: str) -> Optional[float]:
    snaps = db.get_latest_two_iv_snapshots(conn, ticker, expiry, tag)
    if len(snaps) < 2:
        return None
    latest, prev = snaps[0], snaps[1]
    if latest.get("iv") is None or prev.get("iv") is None:
        return None
    return float(latest["iv"]) - float(prev["iv"])


def _compute_iv_confirmation(conn, trade: dict) -> int:
    """
    Simple strike-IV confirmation score:
      1  -> confirms direction/expansion
      0  -> neutral / insufficient data
      -1 -> contradicts setup
    """
    ticker = trade.get("ticker")
    expiry = trade.get("expiry_date")
    opt_type = trade.get("option_type", "")
    if not ticker or not expiry:
        return 0

    call_d = _iv_delta(conn, ticker, expiry, "CALL_WALL")
    put_d = _iv_delta(conn, ticker, expiry, "PUT_WALL")
    atm_d = _iv_delta(conn, ticker, expiry, "ATM")
    thresh = 0.003  # ~30 bps absolute IV move between checks

    if opt_type == "CALL":
        if call_d is None and atm_d is None:
            return 0
        d = call_d if call_d is not None else atm_d
        return 1 if d > thresh else -1 if d < -thresh else 0
    if opt_type == "PUT":
        if put_d is None and atm_d is None:
            return 0
        d = put_d if put_d is not None else atm_d
        return 1 if d > thresh else -1 if d < -thresh else 0
    if opt_type in ("STRADDLE", "STRANGLE"):
        if call_d is None or put_d is None:
            return 0
        if call_d > thresh and put_d > thresh:
            return 1
        if call_d < -thresh and put_d < -thresh:
            return -1
        return 0
    return 0


def _trade_has_wall_levels(trade: dict) -> bool:
    snapshot = trade.get("position_snapshot")
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    snapshot = snapshot or {}
    key_levels = snapshot.get("key_levels", {})
    return (
        _level_strike(key_levels.get("call_wall")) is not None
        or _level_strike(key_levels.get("put_wall")) is not None
    )


def _trade_max_pain(trade: dict) -> Optional[float]:
    snapshot = trade.get("position_snapshot")
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    snapshot = snapshot or {}
    key_levels = snapshot.get("key_levels", {})
    return _level_strike(key_levels.get("max_pain"))


def _dist_to_level(spot: Optional[float], level: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    if spot is None or level is None or spot <= 0:
        return None, None
    dist_abs = round(abs(spot - level), 4)
    dist_pct = round(dist_abs / spot * 100, 4)
    return dist_abs, dist_pct


def _optional_exits_enabled() -> bool:
    return EXIT_ON_SIGNAL_FLIP or EXIT_ON_REGIME_BREAK or EXIT_ON_IV_CONFIRMATION_LOSS


def _compute_optional_signal_exit(
    trade: dict,
    live_ctx: Optional[dict],
    prev_iv_confirmation: Optional[int],
    iv_confirmation: int,
) -> Optional[str]:
    """
    Optional dynamic exits (all disabled by default via config flags).
    """
    if EXIT_ON_SIGNAL_FLIP and live_ctx:
        prev_thesis = trade.get("thesis")
        new_thesis = live_ctx.get("thesis")
        if prev_thesis and new_thesis and prev_thesis != new_thesis:
            return "SIGNAL_FLIP"

    if EXIT_ON_REGIME_BREAK and live_ctx:
        prev_regime = trade.get("reynolds_regime")
        new_regime = live_ctx.get("reynolds_regime")
        if prev_regime and new_regime and prev_regime != new_regime:
            return "REGIME_BREAK"

    if EXIT_ON_IV_CONFIRMATION_LOSS:
        if prev_iv_confirmation == 1 and iv_confirmation == -1:
            return "CONFIRMATION_LOSS"

    return None


def check_trade(trade: dict, conn, iv_confirmation: int = 0,
                spot: Optional[float] = None, chain=None,
                live_ctx: Optional[dict] = None) -> tuple[Optional[str], bool]:
    """
    Evaluate one open trade against all exit conditions.
    Returns the exit reason if closed, or None if still open.
    """
    ticker = trade["ticker"]
    spot = spot if spot is not None else pricing.get_spot(ticker)
    if spot is None:
        log.warning("could not get spot for %s, skipping", ticker)
        return None, True

    current_premium = _get_current_premium(trade, spot, chain=chain)
    degraded_mark = current_premium is None

    unrealized = None
    entry_p = trade.get("entry_premium")
    if entry_p and current_premium and entry_p > 0:
        unrealized = round((current_premium - entry_p) / entry_p * 100, 2)

    max_pain = _trade_max_pain(trade)
    dist_to_mp_abs, dist_to_mp_pct = _dist_to_level(spot, max_pain)

    prev_iv_confirmation = db.get_latest_iv_confirmation(conn, trade["id"])
    db.insert_price_check(
        conn,
        trade["id"],
        spot,
        current_premium,
        unrealized,
        iv_confirmation=iv_confirmation,
        max_pain_level=max_pain,
        dist_to_max_pain_abs=dist_to_mp_abs,
        dist_to_max_pain_pct=dist_to_mp_pct,
        pricing_degraded_flag=int(degraded_mark),
    )

    exit_reason = None

    if _check_expiry(trade):
        exit_reason = "EXPIRY"
    elif _check_target(trade, spot):
        exit_reason = "TARGET"
    elif _check_stop(trade, current_premium):
        exit_reason = "STOP"
    elif _optional_exits_enabled():
        exit_reason = _compute_optional_signal_exit(
            trade, live_ctx, prev_iv_confirmation, iv_confirmation
        )
    elif _check_time_limit(trade):
        exit_reason = "TIME_LIMIT"

    if exit_reason:
        pnl_dollars, pnl_pct = _compute_pnl(entry_p, current_premium)
        min_dist = db.get_min_dist_to_max_pain_pct(conn, trade["id"])
        hit_max_pain = None if min_dist is None else int(min_dist <= 0.25)
        entry_dist_pct = None
        if max_pain is not None and trade.get("entry_spot"):
            try:
                entry_spot = float(trade.get("entry_spot"))
                if entry_spot > 0:
                    entry_dist_pct = round(abs(entry_spot - max_pain) / entry_spot * 100, 4)
            except Exception:
                entry_dist_pct = None
        dist_compression_pct = None
        if entry_dist_pct is not None and dist_to_mp_pct is not None:
            dist_compression_pct = round(entry_dist_pct - dist_to_mp_pct, 4)
        db.close_trade(
            conn, trade["id"],
            exit_spot=spot,
            exit_premium=current_premium,
            exit_reason=exit_reason,
            pnl_dollars=pnl_dollars,
            pnl_pct=pnl_pct,
            min_dist_to_max_pain_pct=min_dist,
            hit_max_pain_flag=hit_max_pain,
            dist_compression_pct=dist_compression_pct,
        )
        log.info("closed trade #%d %s %s %.0f — %s (P&L: %s%%)",
                 trade["id"], ticker, trade.get("option_type"),
                 trade.get("strike", 0), exit_reason,
                 pnl_pct if pnl_pct is not None else "?")
        return exit_reason, degraded_mark

    return None, degraded_mark


def check_all_open() -> dict:
    """Check all open trades. Returns {trade_id: exit_reason or 'OPEN'}."""
    return check_all_open_with_meta()["results"]


def check_all_open_with_meta() -> dict:
    """Check all open trades with metadata (e.g., degraded pricing count)."""
    db.init_db()
    results = {}
    degraded_count = 0
    with db.get_conn() as conn:
        open_trades = db.get_open_trades(conn)
        if not open_trades:
            log.info("no open trades")
            return {"results": results, "degraded_count": degraded_count}

        log.info("checking %d open trades ...", len(open_trades))
        # Pick the best representative trade per (ticker, expiry) for IV snapshots.
        snapshot_trade_by_key = {}
        for t in open_trades:
            key = (t.get("ticker"), t.get("expiry_date"))
            current = snapshot_trade_by_key.get(key)
            if current is None or (not _trade_has_wall_levels(current) and _trade_has_wall_levels(t)):
                snapshot_trade_by_key[key] = t

        snap_done = set()
        spot_cache = {}
        chain_cache = {}
        live_ctx_cache = {}
        for trade in open_trades:
            try:
                # Snapshot strike-IV once per ticker+expiry per check cycle.
                key = (trade.get("ticker"), trade.get("expiry_date"))
                ticker = trade["ticker"]
                if ticker not in spot_cache:
                    spot_cache[ticker] = pricing.get_spot(ticker)
                spot = spot_cache[ticker]
                if key not in chain_cache and key[1]:
                    chain_cache[key] = pricing.get_option_chain_cached(key[0], key[1])
                chain = chain_cache.get(key)
                if _optional_exits_enabled() and ticker not in live_ctx_cache:
                    dm = fetch_dealer_map(ticker)
                    if dm:
                        live_ctx_cache[ticker] = {
                            "thesis": (dm.get("directional") or {}).get("thesis"),
                            "reynolds_regime": (dm.get("reynolds") or {}).get("regime"),
                        }
                    else:
                        live_ctx_cache[ticker] = None
                if spot is not None and key not in snap_done:
                    rep_trade = snapshot_trade_by_key.get(key, trade)
                    _snapshot_key_strike_iv(conn, rep_trade, spot, chain=chain)
                    snap_done.add(key)

                iv_confirmation = _compute_iv_confirmation(conn, trade)
                reason, degraded = check_trade(
                    trade,
                    conn,
                    iv_confirmation=iv_confirmation,
                    spot=spot,
                    chain=chain,
                    live_ctx=live_ctx_cache.get(ticker),
                )
                if degraded:
                    degraded_count += 1
                results[trade["id"]] = reason or "OPEN"
            except Exception as exc:
                log.error("check failed for trade #%d: %s", trade["id"], exc)
                results[trade["id"]] = f"ERROR: {exc}"

    return {"results": results, "degraded_count": degraded_count}
