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
from .config import (
    API_BASE,
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_MAX_HOLD_DAYS,
    CHALLENGER_V1_ENABLED,
    CHALLENGER_MIN_DIRECTIONAL_DTE,
    CHALLENGER_BREAKOUT_MIN_CONFIDENCE,
    CHALLENGER_DTE_BYPASS_MIN_CONFIDENCE,
    CHALLENGER_UNCONFIRMED_STOP_PCT,
    CHALLENGER_V2_ENABLED,
    CH_V2_MIN_DIRECTIONAL_DTE,
    CH_V2_BREAKOUT_MIN_CONFIDENCE,
    CH_V2_DTE_BYPASS_MIN_CONFIDENCE,
    CH_V2_UNCONFIRMED_STOP_PCT,
    CH_V2_BREAKOUT_REQUIRE_WITH_DEALER,
    CH_V2_BLOCK_THESES,
    CHALLENGER_V3_ENABLED,
    CH_V3_BLOCK_AGAINST_DEALER,
    CH_V3_AGAINST_DEALER_OVERRIDE_MIN_CONF,
    CH_V3_MAX_REYNOLDS_DIRECTIONAL,
    CH_V3_REQUIRE_POS_GAMMA_LAMINAR,
    CH_V3_MIN_DIRECTIONAL_DTE,
    CH_V3_DTE_BYPASS_MIN_CONFIDENCE,
    CH_V3_UNCONFIRMED_STOP_PCT,
    CH_V3_BLOCK_THESES,
    CH_V3_BLOCKED_TICKERS,
    CH_V3_SKIP_STRADDLE_TURBULENT,
)

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


_CONFIDENCE_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def _confidence_at_least(confidence: Optional[str], minimum: str) -> bool:
    current = _CONFIDENCE_ORDER.get((confidence or "").upper(), -1)
    required = _CONFIDENCE_ORDER.get((minimum or "").upper(), 99)
    return current >= required


def _is_breakout_thesis(thesis: Optional[str]) -> bool:
    return (thesis or "").upper() == "MOMENTUM_BREAKOUT"


def _active_challenger_profile() -> Optional[str]:
    if CHALLENGER_V3_ENABLED:
        return "v3"
    if CHALLENGER_V2_ENABLED:
        return "v2"
    if CHALLENGER_V1_ENABLED:
        return "v1"
    return None


def _blocked_theses_v2() -> set[str]:
    raw = (CH_V2_BLOCK_THESES or "").strip()
    if not raw:
        return set()
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


def _blocked_theses_v3() -> set[str]:
    raw = (CH_V3_BLOCK_THESES or "").strip()
    if not raw:
        return set()
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


def _challenger_directional_reject_reason(
    pos: dict, directional: dict, dte: int, response: dict | None = None
) -> Optional[str]:
    profile = _active_challenger_profile()
    if not profile:
        return None

    thesis = directional.get("thesis")
    conf = pos.get("confidence")
    thesis_upper = (thesis or "").upper()
    edge_type = (pos.get("edge_type") or "").upper()

    if profile == "v3":
        blocked = _blocked_theses_v3()
        if thesis_upper in blocked:
            return "blocked_thesis"

        if CH_V3_BLOCK_AGAINST_DEALER and edge_type == "AGAINST_DEALER":
            if not _confidence_at_least(conf, CH_V3_AGAINST_DEALER_OVERRIDE_MIN_CONF):
                return "against_dealer_block"
            reynolds = (response or {}).get("reynolds", {})
            if (reynolds.get("regime") or "").upper() != "LAMINAR":
                return "against_dealer_not_laminar"

        reynolds = (response or {}).get("reynolds", {})
        re_num = reynolds.get("number")
        if re_num is not None and re_num > CH_V3_MAX_REYNOLDS_DIRECTIONAL:
            if not (
                _is_breakout_thesis(thesis)
                and edge_type == "WITH_DEALER"
                and _confidence_at_least(conf, "HIGH")
            ):
                return "reynolds_gate"

        if CH_V3_REQUIRE_POS_GAMMA_LAMINAR:
            gex = (response or {}).get("gex_regime", "")
            re_regime = (reynolds.get("regime") or "").upper()
            if not (gex == "POSITIVE_GAMMA" and re_regime == "LAMINAR"):
                return "require_pg_laminar"

        high_conf = _confidence_at_least(conf, CH_V3_DTE_BYPASS_MIN_CONFIDENCE)
        if (dte or 0) < CH_V3_MIN_DIRECTIONAL_DTE and not high_conf:
            return "dte_gate"
        return None

    if profile == "v2":
        min_dte = CH_V2_MIN_DIRECTIONAL_DTE
        breakout_min_conf = CH_V2_BREAKOUT_MIN_CONFIDENCE
        dte_bypass_min_conf = CH_V2_DTE_BYPASS_MIN_CONFIDENCE
        blocked = _blocked_theses_v2()
        if thesis_upper in blocked:
            return "blocked_thesis"
        if (
            _is_breakout_thesis(thesis)
            and CH_V2_BREAKOUT_REQUIRE_WITH_DEALER
            and edge_type != "WITH_DEALER"
        ):
            return "breakout_not_with_dealer"
    else:
        min_dte = CHALLENGER_MIN_DIRECTIONAL_DTE
        breakout_min_conf = CHALLENGER_BREAKOUT_MIN_CONFIDENCE
        dte_bypass_min_conf = CHALLENGER_DTE_BYPASS_MIN_CONFIDENCE

    high_conf = _confidence_at_least(conf, dte_bypass_min_conf)
    if _is_breakout_thesis(thesis) and not _confidence_at_least(conf, breakout_min_conf):
        return "breakout_low_confidence"
    if (dte or 0) < min_dte and not high_conf:
        return "dte_gate"
    return None


def _iv_confirmed_entry_proxy(pos: dict) -> bool:
    """
    Entry-time IV confirmation is not yet exposed directly in API payloads.
    For challenger_v1, use HIGH confidence as conservative proxy.
    """
    return _confidence_at_least(pos.get("confidence"), "HIGH")


def _challenger_directional_allowed(pos: dict, directional: dict, dte: int,
                                    response: dict | None = None) -> bool:
    return _challenger_directional_reject_reason(pos, directional, dte, response) is None


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


def _one_day_return_pct(symbol: str) -> Optional[float]:
    rows = pricing.get_daily_closes(symbol, period="10d")
    if len(rows) < 2:
        return None
    prev_close = rows[-2].get("close")
    last_close = rows[-1].get("close")
    if not prev_close or prev_close <= 0 or last_close is None:
        return None
    return round((float(last_close) - float(prev_close)) / float(prev_close) * 100.0, 4)


def _build_market_context() -> dict:
    return {
        "vix": pricing.get_spot("^VIX"),
        "vvix": pricing.get_spot("^VVIX"),
        "tnx": pricing.get_spot("^TNX"),
        "dxy": pricing.get_spot("DX-Y.NYB"),
        "spy": pricing.get_spot("SPY"),
        "qqq": pricing.get_spot("QQQ"),
        "iwm": pricing.get_spot("IWM"),
        "spy_ret_1d_pct": _one_day_return_pct("SPY"),
        "qqq_ret_1d_pct": _one_day_return_pct("QQQ"),
        "iwm_ret_1d_pct": _one_day_return_pct("IWM"),
    }


def _log_no_trade(
    conn,
    ticker: str,
    candidate_kind: str,
    reason_code: str,
    scan_id: Optional[int] = None,
    pos: Optional[dict] = None,
    response: Optional[dict] = None,
    metadata: Optional[dict] = None,
):
    pos = pos or {}
    directional = (response or {}).get("directional", {})
    reynolds = (response or {}).get("reynolds", {})
    db.insert_no_trade_event(
        conn,
        {
            "scan_id": scan_id,
            "ticker": ticker,
            "candidate_kind": candidate_kind,
            "reason_code": reason_code,
            "trade_type": (
                "directional" if candidate_kind == "DIRECTIONAL"
                else "straddle" if candidate_kind == "STRADDLE"
                else None
            ),
            "option_type": pos.get("option_type"),
            "strike": pos.get("strike"),
            "confidence": pos.get("confidence"),
            "thesis": directional.get("thesis"),
            "edge_type": pos.get("edge_type"),
            "reynolds_number": reynolds.get("number"),
            "reynolds_regime": reynolds.get("regime"),
            "dte": (response or {}).get("dte"),
            "metadata": metadata or {},
        },
    )


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

    if not _challenger_directional_allowed(pos, directional, dte, response):
        return None

    fallback_target = _wall_fallback(response, option_type)
    target_price = _parse_target_price(pos.get("target", ""), fallback_target)
    stop_loss_pct = _parse_stop_loss_pct(pos.get("stop", ""))
    profile = _active_challenger_profile()
    if profile and not _iv_confirmed_entry_proxy(pos):
        if profile == "v3":
            stop_cap = CH_V3_UNCONFIRMED_STOP_PCT
        elif profile == "v2":
            stop_cap = CH_V2_UNCONFIRMED_STOP_PCT
        else:
            stop_cap = CHALLENGER_UNCONFIRMED_STOP_PCT
        stop_loss_pct = min(stop_loss_pct, stop_cap)
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


def _build_directional_signal(pos: dict, response: dict, spot: float) -> Optional[dict]:
    if pos.get("action") == "WAIT" or pos.get("type") == "skip":
        return None
    strike = pos.get("strike", 0)
    option_type = pos.get("option_type", "")
    if not strike or option_type == "—":
        return None
    direction = "UP" if option_type == "CALL" else "DOWN" if option_type == "PUT" else "VOL"
    directional = response.get("directional", {})
    return {
        "ticker": response["ticker"],
        "signal_kind": "DIRECTIONAL",
        "direction": direction,
        "option_type": option_type,
        "strike": strike,
        "target_price": _parse_target_price(pos.get("target", ""), _wall_fallback(response, option_type)),
        "entry_spot": spot,
        "entry_time": datetime.utcnow().isoformat(),
        "expiry_date": response.get("expiration"),
        "dte_at_entry": response.get("dte", 0),
        "thesis": directional.get("thesis"),
        "edge_type": pos.get("edge_type"),
        "confidence": pos.get("confidence"),
        "metadata": {
            "name": pos.get("name"),
            "stop_text": pos.get("stop"),
            "target_text": pos.get("target"),
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


def _build_straddle_signal(response: dict, spot: float) -> Optional[dict]:
    sa = response.get("straddle_analysis", {})
    verdict = sa.get("verdict", "")
    if verdict not in ("BUY_STRADDLE", "BUY_STRANGLE", "CONSIDER"):
        return None

    straddle = sa.get("straddle", {})
    strangle = sa.get("strangle", {})
    directional = response.get("directional", {})
    kind = "STRANGLE" if verdict == "BUY_STRANGLE" else "STRADDLE"
    required_move = straddle.get("required_move_pct") or strangle.get("required_move_pct")
    return {
        "ticker": response["ticker"],
        "signal_kind": kind,
        "direction": "VOL",
        "option_type": kind,
        "strike": straddle.get("strike"),
        "target_price": straddle.get("upper_breakeven") or strangle.get("upper_breakeven"),
        "entry_spot": spot,
        "entry_time": datetime.utcnow().isoformat(),
        "expiry_date": response.get("expiration"),
        "dte_at_entry": response.get("dte", 0),
        "thesis": directional.get("thesis"),
        "edge_type": "NEUTRAL",
        "confidence": None,
        "metadata": {
            "verdict": verdict,
            "required_move_pct": required_move,
            "upper_breakeven": straddle.get("upper_breakeven") or strangle.get("upper_breakeven"),
            "lower_breakeven": straddle.get("lower_breakeven") or strangle.get("lower_breakeven"),
        },
    }


# ── public entry point ───────────────────────────────────────────

def scan_ticker(
    ticker: str,
    conn,
    account_size: Optional[float] = None,
    market_context: Optional[dict] = None,
) -> list[int]:
    """
    Scan one ticker: call API, open paper trades for actionable signals.
    Returns list of new trade IDs.
    """
    if CHALLENGER_V3_ENABLED and ticker.upper() in CH_V3_BLOCKED_TICKERS:
        log.info("v3: skip blocked ticker %s", ticker)
        _log_no_trade(
            conn,
            ticker=ticker,
            candidate_kind="TICKER",
            reason_code="blocked_ticker",
            metadata={"profile": "v3"},
        )
        return []

    response = fetch_dealer_map(ticker, account_size)
    if not response:
        return []

    spot = response.get("spot", 0)
    if not spot:
        spot = pricing.get_spot(ticker) or 0

    scan_id = db.insert_scan(conn, ticker, spot, response, market_context=market_context)

    trade_ids = []
    directional = response.get("directional", {})

    for pos in directional.get("positions", []):
        signal_id = None
        sig = _build_directional_signal(pos, response, spot)
        if sig:
            signal_id = db.insert_signal(conn, scan_id, sig)

        if pos.get("action") == "WAIT" or pos.get("type") == "skip":
            _log_no_trade(
                conn,
                ticker=ticker,
                candidate_kind="DIRECTIONAL",
                reason_code="wait_or_skip",
                scan_id=scan_id,
                pos=pos,
                response=response,
            )
            continue
        if not pos.get("strike") or pos.get("option_type") == "—":
            _log_no_trade(
                conn,
                ticker=ticker,
                candidate_kind="DIRECTIONAL",
                reason_code="invalid_contract",
                scan_id=scan_id,
                pos=pos,
                response=response,
            )
            continue
        gate_reason = _challenger_directional_reject_reason(
            pos, directional, response.get("dte", 0), response
        )
        if gate_reason:
            _log_no_trade(
                conn,
                ticker=ticker,
                candidate_kind="DIRECTIONAL",
                reason_code=gate_reason,
                scan_id=scan_id,
                pos=pos,
                response=response,
            )
            continue

        trade = _build_directional_trade(pos, response, spot)
        if not trade:
            _log_no_trade(
                conn,
                ticker=ticker,
                candidate_kind="DIRECTIONAL",
                reason_code="trade_build_failed",
                scan_id=scan_id,
                pos=pos,
                response=response,
            )
            continue
        if db.has_open_trade(conn, ticker, trade["strike"],
                            trade["option_type"], trade.get("expiry_date", "")):
            log.info("skip duplicate: %s %s %.0f %s",
                     ticker, trade["option_type"], trade["strike"],
                     trade["expiry_date"])
            _log_no_trade(
                conn,
                ticker=ticker,
                candidate_kind="DIRECTIONAL",
                reason_code="duplicate_open_trade",
                scan_id=scan_id,
                pos=pos,
                response=response,
                metadata={"expiry_date": trade.get("expiry_date")},
            )
            continue
        tid = db.insert_trade(conn, scan_id, ticker, trade, source_signal_id=signal_id)
        trade_ids.append(tid)
        log.info("opened trade #%d: %s %s %.0f %s (premium=%.2f)",
                 tid, ticker, trade["option_type"], trade["strike"],
                 trade["expiry_date"] or "?",
                 trade.get("entry_premium") or 0)

    straddle_trade = _build_straddle_trade(response, spot)
    straddle_skip_reason = None
    if straddle_trade is None:
        straddle_skip_reason = "straddle_not_actionable"
    if CHALLENGER_V3_ENABLED and CH_V3_SKIP_STRADDLE_TURBULENT:
        re_regime = (response.get("reynolds", {}).get("regime") or "").upper()
        if re_regime == "TURBULENT":
            straddle_trade = None
            straddle_skip_reason = "straddle_turbulent"
    straddle_sig = _build_straddle_signal(response, spot)
    straddle_signal_id = None
    if straddle_sig:
        straddle_signal_id = db.insert_signal(conn, scan_id, straddle_sig)

    if straddle_trade:
        if not db.has_open_trade(conn, ticker, straddle_trade["strike"],
                                straddle_trade["option_type"],
                                straddle_trade.get("expiry_date", "")):
            tid = db.insert_trade(
                conn,
                scan_id,
                ticker,
                straddle_trade,
                source_signal_id=straddle_signal_id,
            )
            trade_ids.append(tid)
            log.info("opened straddle #%d: %s %s %.0f (premium=%.2f)",
                     tid, ticker, straddle_trade["option_type"],
                     straddle_trade["strike"],
                     straddle_trade.get("entry_premium") or 0)
        else:
            _log_no_trade(
                conn,
                ticker=ticker,
                candidate_kind="STRADDLE",
                reason_code="duplicate_open_trade",
                scan_id=scan_id,
                response=response,
                metadata={
                    "option_type": straddle_trade.get("option_type"),
                    "strike": straddle_trade.get("strike"),
                    "expiry_date": straddle_trade.get("expiry_date"),
                },
            )
    elif straddle_skip_reason:
        _log_no_trade(
            conn,
            ticker=ticker,
            candidate_kind="STRADDLE",
            reason_code=straddle_skip_reason,
            scan_id=scan_id,
            response=response,
            metadata={"verdict": (response.get("straddle_analysis", {}) or {}).get("verdict")},
        )

    return trade_ids


def scan_watchlist(tickers: list[str], account_size: Optional[float] = None) -> dict:
    """Scan multiple tickers. Returns {ticker: [trade_ids]}."""
    db.init_db()
    results = {}
    market_context = _build_market_context()
    with db.get_conn() as conn:
        for ticker in tickers:
            ticker = ticker.upper().strip()
            log.info("scanning %s ...", ticker)
            try:
                ids = scan_ticker(ticker, conn, account_size, market_context=market_context)
                results[ticker] = ids
            except Exception as exc:
                log.error("scan failed for %s: %s", ticker, exc)
                results[ticker] = []
    return results
