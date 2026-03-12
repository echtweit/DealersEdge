"""
Option pricing via yfinance.
Fetches actual bid/ask from the live chain for realistic paper-trade pricing.
"""
import logging
import time
from collections import deque
from datetime import datetime
from typing import Optional

import yfinance as yf
import pandas as pd

from .config import (
    CHAIN_CACHE_TTL_SEC,
    CHAIN_STALE_GRACE_SEC,
    CHAIN_FETCH_MAX_PER_MIN,
    DAILY_CLOSE_CACHE_TTL_SEC,
    DAILY_CLOSE_FETCH_MAX_PER_MIN,
)

log = logging.getLogger(__name__)

# In-process cache + light rate limiter for option-chain calls.
_CHAIN_CACHE: dict[tuple[str, str], dict] = {}
_CHAIN_FETCH_TIMES = deque()
_DAILY_CLOSE_CACHE: dict[tuple[str, str], dict] = {}
_DAILY_CLOSE_FETCH_TIMES = deque()


def _chain_rate_limited() -> bool:
    now = time.time()
    window_start = now - 60
    while _CHAIN_FETCH_TIMES and _CHAIN_FETCH_TIMES[0] < window_start:
        _CHAIN_FETCH_TIMES.popleft()
    return len(_CHAIN_FETCH_TIMES) >= CHAIN_FETCH_MAX_PER_MIN


def _daily_close_rate_limited() -> bool:
    now = time.time()
    window_start = now - 60
    while _DAILY_CLOSE_FETCH_TIMES and _DAILY_CLOSE_FETCH_TIMES[0] < window_start:
        _DAILY_CLOSE_FETCH_TIMES.popleft()
    return len(_DAILY_CLOSE_FETCH_TIMES) >= DAILY_CLOSE_FETCH_MAX_PER_MIN


def _chain_cache_get(ticker: str, expiry: str, max_age_s: int) -> Optional[dict]:
    key = (ticker, expiry)
    entry = _CHAIN_CACHE.get(key)
    if not entry:
        return None
    age = time.time() - entry["ts"]
    return entry if age <= max_age_s else None


def _chain_cache_set(ticker: str, expiry: str, chain):
    key = (ticker, expiry)
    _CHAIN_CACHE[key] = {"ts": time.time(), "chain": chain}


def _fetch_chain(ticker: str, expiry: str):
    stock = yf.Ticker(ticker)
    return stock.option_chain(expiry)


def get_option_chain_cached(ticker: str, expiry: str, force_refresh: bool = False):
    """
    Get option chain with TTL caching and per-minute fetch budget.
    Returns chain object or None.
    """
    # Fresh cache hit
    if not force_refresh:
        hit = _chain_cache_get(ticker, expiry, CHAIN_CACHE_TTL_SEC)
        if hit:
            return hit["chain"]

    # Budget guard; return stale cache when available.
    if _chain_rate_limited():
        stale = _chain_cache_get(ticker, expiry, CHAIN_STALE_GRACE_SEC)
        if stale:
            log.debug("rate-limited; using stale chain for %s %s", ticker, expiry)
            return stale["chain"]
        log.warning("rate limit budget reached and no cache for %s %s", ticker, expiry)
        return None

    try:
        chain = _fetch_chain(ticker, expiry)
        _CHAIN_FETCH_TIMES.append(time.time())
        _chain_cache_set(ticker, expiry, chain)
        return chain
    except Exception as exc:
        # On fetch failure, fall back to stale cache if available.
        stale = _chain_cache_get(ticker, expiry, CHAIN_STALE_GRACE_SEC)
        if stale:
            log.warning("chain fetch failed for %s %s (%s); using stale cache", ticker, expiry, exc)
            return stale["chain"]
        log.warning("chain fetch failed for %s %s: %s", ticker, expiry, exc)
        return None


def _select_row(df: pd.DataFrame, strike: float):
    if df.empty or "strike" not in df.columns:
        return None
    row = df.loc[df["strike"] == strike]
    if row.empty:
        closest_idx = (df["strike"] - strike).abs().idxmin()
        row = df.loc[[closest_idx]]
    return row.iloc[0]


def get_spot(ticker: str) -> Optional[float]:
    try:
        stock = yf.Ticker(ticker)
        info = stock.fast_info
        price = info.get("lastPrice") or info.get("previousClose")
        return float(price) if price else None
    except Exception as exc:
        log.warning("spot lookup failed for %s: %s", ticker, exc)
        return None


def get_option_mid(ticker: str, expiry: str, strike: float,
                   option_type: str, chain=None) -> Optional[float]:
    """
    Look up the mid-price for a specific contract.

    Parameters
    ----------
    ticker : str
    expiry : str      YYYY-MM-DD
    strike : float
    option_type : str  CALL / PUT

    Returns the (bid+ask)/2 mid-price per share, or None on failure.
    """
    chain = chain or get_option_chain_cached(ticker, expiry)
    if chain is None:
        return None

    df = chain.calls if option_type.upper() == "CALL" else chain.puts
    r = _select_row(df, strike)
    if r is None:
        return None
    bid = float(r.get("bid", 0) or 0)
    ask = float(r.get("ask", 0) or 0)

    if bid > 0 and ask > 0:
        return round((bid + ask) / 2, 4)
    last = float(r.get("lastPrice", 0) or 0)
    if last > 0:
        return round(last, 4)

    return None


def get_straddle_mid(ticker: str, expiry: str,
                     strike: float, chain=None) -> Optional[float]:
    """
    Mid-price for an ATM straddle (call + put at same strike).
    Returns total per-share cost, or None.
    """
    chain = chain or get_option_chain_cached(ticker, expiry)
    if chain is None:
        return None
    call_mid = get_option_mid(ticker, expiry, strike, "CALL", chain=chain)
    put_mid = get_option_mid(ticker, expiry, strike, "PUT", chain=chain)
    if call_mid is not None and put_mid is not None:
        return round(call_mid + put_mid, 4)
    return None


def get_strangle_mid(ticker: str, expiry: str,
                     call_strike: float,
                     put_strike: float, chain=None) -> Optional[float]:
    chain = chain or get_option_chain_cached(ticker, expiry)
    if chain is None:
        return None
    call_mid = get_option_mid(ticker, expiry, call_strike, "CALL", chain=chain)
    put_mid = get_option_mid(ticker, expiry, put_strike, "PUT", chain=chain)
    if call_mid is not None and put_mid is not None:
        return round(call_mid + put_mid, 4)
    return None


def get_strike_quote(ticker: str, expiry: str, strike: float, side: str, chain=None) -> Optional[dict]:
    """
    Return quote/IV for the closest available strike on requested side.
    side: CALL or PUT
    """
    chain = chain or get_option_chain_cached(ticker, expiry)
    if chain is None:
        return None

    df = chain.calls if side.upper() == "CALL" else chain.puts
    r = _select_row(df, strike)
    if r is None:
        return None
    return {
        "strike": float(r.get("strike", strike)),
        "iv": float(r.get("impliedVolatility", 0) or 0),
        "bid": float(r.get("bid", 0) or 0),
        "ask": float(r.get("ask", 0) or 0),
        "open_interest": int(r.get("openInterest", 0) or 0),
        "volume": int(r.get("volume", 0) or 0),
    }


def find_nearest_expiry(ticker: str, target_dte: int) -> Optional[str]:
    """Find the expiry closest to target_dte days from now."""
    try:
        stock = yf.Ticker(ticker)
        today = datetime.now().date()
        best, best_diff = None, 9999
        for exp_str in stock.options:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            diff = abs((exp_date - today).days - target_dte)
            if diff < best_diff:
                best, best_diff = exp_str, diff
        return best
    except Exception as exc:
        log.warning("expiry lookup failed for %s: %s", ticker, exc)
        return None


def get_daily_closes(ticker: str, period: str = "2mo") -> list[dict]:
    """Return daily closes: [{'date': 'YYYY-MM-DD', 'close': float}, ...]."""
    key = (ticker, period)
    cached = _DAILY_CLOSE_CACHE.get(key)
    if cached and (time.time() - cached["ts"]) <= DAILY_CLOSE_CACHE_TTL_SEC:
        return cached["rows"]

    if _daily_close_rate_limited():
        if cached:
            log.debug("daily-close rate-limited; using stale cache for %s", ticker)
            return cached["rows"]
        log.warning("daily-close rate limit budget reached for %s", ticker)
        return []

    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period, interval="1d")
        if df.empty:
            return []
        rows = []
        for ts, row in df.iterrows():
            rows.append({"date": ts.strftime("%Y-%m-%d"), "close": float(row["Close"])})
        _DAILY_CLOSE_FETCH_TIMES.append(time.time())
        _DAILY_CLOSE_CACHE[key] = {"ts": time.time(), "rows": rows}
        return rows
    except Exception as exc:
        if cached:
            log.warning("daily close fetch failed for %s (%s); using stale cache", ticker, exc)
            return cached["rows"]
        log.warning("daily close fetch failed for %s: %s", ticker, exc)
        return []
