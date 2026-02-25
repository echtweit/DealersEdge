"""
Scanner: calls DealersEdge API, parses exit rules, opens paper trades.
"""
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

import requests

from . import db, pricing
from .config import API_BASE, DEFAULT_STOP_LOSS_PCT, DEFAULT_MAX_HOLD_DAYS

log = logging.getLogger(__name__)


# ── exit-rule parsing ────────────────────────────────────────────

_DOLLAR_RE = re.compile(r"\$([0-9,]+(?:\.[0-9]+)?)")
_PREMIUM_STOP_RE = re.compile(r"(?:cut|stop|exit|close).*?(-?\d+(?:\.\d+)?)\s*%", re.IGNORECASE)
_GENERIC_PCT_RE = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*%\s*(?:of\s+premium|premium\s+loss|loss)",
    re.IGNORECASE,
)
_DTE_RANGE_RE = re.compile(r"(\d+)\s*-\s*(\d+)\s*DTE", re.IGNORECASE)
_DTE_SINGLE_RE = re.compile(r"(\d+)\s*DTE", re.IGNORECASE)


def _level_strike(level) -> Optional[float]:
    """Normalize key level values to numeric strike."""
    if isinstance(level, (int, float)):
        return float(level)
    if isinstance(level, dict):
        strike = level.get("strike")
        if isinstance(strike, (int, float)):
            return float(strike)
    return None


def _parse_target_price(target_text: str, fallback: Optional[float] = None) -> Optional[float]:
    m = _DOLLAR_RE.search(target_text)
    if m:
        return float(m.group(1).replace(",", ""))
    return fallback


def _parse_stop_loss_pct(stop_text: str) -> float:
    # Look for premium-specific patterns first ("X% of premium", "cut at -X%")
    m = _GENERIC_PCT_RE.search(stop_text)
    if m:
        val = abs(float(m.group(1)))
        if 10 <= val <= 90:
            return val

    m = _PREMIUM_STOP_RE.search(stop_text)
    if m:
        val = abs(float(m.group(1)))
        if 10 <= val <= 90:
            return val

    return DEFAULT_STOP_LOSS_PCT


def _parse_max_hold_days(dte_guidance: str, dte_at_entry: int) -> int:
    m = _DTE_RANGE_RE.search(dte_guidance)
    if m:
        return int(m.group(2))
    m = _DTE_SINGLE_RE.search(dte_guidance)
    if m:
        return int(m.group(1))
    return min(dte_at_entry, DEFAULT_MAX_HOLD_DAYS) if dte_at_entry else DEFAULT_MAX_HOLD_DAYS


def _wall_fallback(response: dict, side: str) -> Optional[float]:
    """Extract call_wall or put_wall price from the full API response."""
    key_levels = response.get("key_levels", {})
    if side == "CALL":
        val = _level_strike(key_levels.get("call_wall"))
    else:
        val = _level_strike(key_levels.get("put_wall"))
    return val


# ── API call ─────────────────────────────────────────────────────

def fetch_dealer_map(ticker: str, account_size: Optional[float] = None) -> Optional[dict]:
    url = f"{API_BASE}/dealer-map/{ticker}"
    params = {}
    if account_size:
        params["account_size"] = account_size
    try:
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.error("API call failed for %s: %s", ticker, exc)
        return None


# ── trade construction ───────────────────────────────────────────

def _build_directional_trade(pos: dict, response: dict,
                             spot: float) -> Optional[dict]:
    """Convert a directional PositionData dict into a trade record."""
    if pos.get("action") == "WAIT" or pos.get("type") == "skip":
        return None

    strike = pos.get("strike", 0)
    option_type = pos.get("option_type", "")
    if not strike or option_type == "—":
        return None

    expiry = response.get("expiration")
    dte = response.get("dte", 0)
    directional = response.get("directional", {})
    reynolds = response.get("reynolds", {})
    acf = response.get("acf_data", {})
    gex_profile = response.get("gex_profile", {})
    entropy = gex_profile.get("entropy", {})
    vol = response.get("vol_analysis", {})
    vrp = vol.get("vrp", {})
    wb = directional.get("wall_break", {})
    key_levels = response.get("key_levels", {})

    fallback_target = _wall_fallback(response, option_type)
    target_price = _parse_target_price(pos.get("target", ""), fallback_target)
    stop_loss_pct = _parse_stop_loss_pct(pos.get("stop", ""))
    max_hold = _parse_max_hold_days(pos.get("dte_guidance", ""), dte)

    entry_premium = pricing.get_option_mid(
        response["ticker"], expiry, strike, option_type
    )

    return {
        "trade_type": "directional",
        "entry_spot": spot,
        "entry_premium": entry_premium,
        "option_type": option_type,
        "strike": strike,
        "expiry_date": expiry,
        "dte_at_entry": dte,
        "contracts": 1,
        "confidence": pos.get("confidence"),
        "kelly_pct": pos.get("kelly_pct"),
        "risk_dollars": pos.get("risk_dollars"),
        "gex_regime": response.get("gex_regime"),
        "reynolds_number": reynolds.get("number"),
        "reynolds_regime": reynolds.get("regime"),
        "acf_regime": acf.get("stability"),
        "entropy_regime": entropy.get("regime"),
        "wall_break_prob": wb.get("probability"),
        "thesis": directional.get("thesis"),
        "edge_type": pos.get("edge_type"),
        "vrp_label": vrp.get("label"),
        "iv_rv_ratio": vol.get("iv_hv", {}).get("iv_hv_ratio"),
        "atm_iv": directional.get("atm_iv"),
        "target_price": target_price,
        "stop_loss_pct": stop_loss_pct,
        "max_hold_days": max_hold,
        "position_snapshot": {
            **pos,
            "key_levels": {
                "call_wall": _level_strike(key_levels.get("call_wall")),
                "put_wall": _level_strike(key_levels.get("put_wall")),
                "max_pain": _level_strike(key_levels.get("max_pain")),
            },
        },
    }


def _build_straddle_trade(response: dict, spot: float) -> Optional[dict]:
    """Convert straddle analysis into a trade record if verdict is actionable."""
    sa = response.get("straddle_analysis", {})
    verdict = sa.get("verdict", "")
    if verdict not in ("BUY_STRADDLE", "BUY_STRANGLE", "CONSIDER"):
        return None

    straddle = sa.get("straddle", {})
    strangle = sa.get("strangle", {})
    expiry = response.get("expiration")
    dte = response.get("dte", 0)
    directional = response.get("directional", {})
    reynolds = response.get("reynolds", {})
    acf = response.get("acf_data", {})
    gex_profile = response.get("gex_profile", {})
    entropy = gex_profile.get("entropy", {})
    vol = response.get("vol_analysis", {})
    vrp = vol.get("vrp", {})
    wb = directional.get("wall_break", {})
    key_levels = response.get("key_levels", {})

    if verdict == "BUY_STRANGLE":
        strike = straddle.get("strike", 0)
        option_type = "STRANGLE"
        entry_premium = pricing.get_strangle_mid(
            response["ticker"], expiry,
            strangle.get("call_strike", 0),
            strangle.get("put_strike", 0),
        )
    else:
        strike = straddle.get("strike", 0)
        option_type = "STRADDLE"
        entry_premium = pricing.get_straddle_mid(
            response["ticker"], expiry, strike,
        )

    upper_be = straddle.get("upper_breakeven") or strangle.get("upper_breakeven")
    lower_be = straddle.get("lower_breakeven") or strangle.get("lower_breakeven")

    return {
        "trade_type": "straddle",
        "entry_spot": spot,
        "entry_premium": entry_premium,
        "option_type": option_type,
        "strike": strike,
        "expiry_date": expiry,
        "dte_at_entry": dte,
        "contracts": 1,
        "confidence": None,
        "kelly_pct": sa.get("risk_pct"),
        "risk_dollars": sa.get("risk_dollars"),
        "gex_regime": response.get("gex_regime"),
        "reynolds_number": reynolds.get("number"),
        "reynolds_regime": reynolds.get("regime"),
        "acf_regime": acf.get("stability"),
        "entropy_regime": entropy.get("regime"),
        "wall_break_prob": wb.get("probability"),
        "thesis": directional.get("thesis"),
        "edge_type": "NEUTRAL",
        "vrp_label": vrp.get("label"),
        "iv_rv_ratio": vol.get("iv_hv", {}).get("iv_hv_ratio"),
        "atm_iv": directional.get("atm_iv"),
        "target_price": upper_be,
        "stop_loss_pct": 50.0,
        "max_hold_days": max(dte - 2, 1) if dte else DEFAULT_MAX_HOLD_DAYS,
        "position_snapshot": {
            "verdict": verdict,
            "verdict_label": sa.get("verdict_label"),
            "score": sa.get("score"),
            "reasoning": sa.get("reasoning"),
            "straddle": straddle,
            "strangle": strangle,
            "upper_breakeven": upper_be,
            "lower_breakeven": lower_be,
            "key_levels": {
                "call_wall": _level_strike(key_levels.get("call_wall")),
                "put_wall": _level_strike(key_levels.get("put_wall")),
                "max_pain": _level_strike(key_levels.get("max_pain")),
            },
        },
    }


# ── public entry point ───────────────────────────────────────────

def scan_ticker(ticker: str, conn, account_size: Optional[float] = None) -> list[int]:
    """
    Scan one ticker: call API, open paper trades for actionable signals.
    Returns list of new trade IDs.
    """
    response = fetch_dealer_map(ticker, account_size)
    if not response:
        return []

    spot = response.get("spot", 0)
    if not spot:
        spot = pricing.get_spot(ticker) or 0

    scan_id = db.insert_scan(conn, ticker, spot, response)

    trade_ids = []
    directional = response.get("directional", {})

    for pos in directional.get("positions", []):
        trade = _build_directional_trade(pos, response, spot)
        if not trade:
            continue
        if db.has_open_trade(conn, ticker, trade["strike"],
                            trade["option_type"], trade.get("expiry_date", "")):
            log.info("skip duplicate: %s %s %.0f %s",
                     ticker, trade["option_type"], trade["strike"],
                     trade["expiry_date"])
            continue
        tid = db.insert_trade(conn, scan_id, ticker, trade)
        trade_ids.append(tid)
        log.info("opened trade #%d: %s %s %.0f %s (premium=%.2f)",
                 tid, ticker, trade["option_type"], trade["strike"],
                 trade["expiry_date"] or "?",
                 trade.get("entry_premium") or 0)

    straddle_trade = _build_straddle_trade(response, spot)
    if straddle_trade:
        if not db.has_open_trade(conn, ticker, straddle_trade["strike"],
                                straddle_trade["option_type"],
                                straddle_trade.get("expiry_date", "")):
            tid = db.insert_trade(conn, scan_id, ticker, straddle_trade)
            trade_ids.append(tid)
            log.info("opened straddle #%d: %s %s %.0f (premium=%.2f)",
                     tid, ticker, straddle_trade["option_type"],
                     straddle_trade["strike"],
                     straddle_trade.get("entry_premium") or 0)

    return trade_ids


def scan_watchlist(tickers: list[str], account_size: Optional[float] = None) -> dict:
    """Scan multiple tickers. Returns {ticker: [trade_ids]}."""
    db.init_db()
    results = {}
    with db.get_conn() as conn:
        for ticker in tickers:
            ticker = ticker.upper().strip()
            log.info("scanning %s ...", ticker)
            try:
                ids = scan_ticker(ticker, conn, account_size)
                results[ticker] = ids
            except Exception as exc:
                log.error("scan failed for %s: %s", ticker, exc)
                results[ticker] = []
    return results
