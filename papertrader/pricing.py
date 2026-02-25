"""
Option pricing via yfinance.
Fetches actual bid/ask from the live chain for realistic paper-trade pricing.
"""
import logging
from datetime import datetime
from typing import Optional

import yfinance as yf

log = logging.getLogger(__name__)


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
                   option_type: str) -> Optional[float]:
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
    try:
        stock = yf.Ticker(ticker)
        chain = stock.option_chain(expiry)
    except Exception as exc:
        log.warning("chain fetch failed for %s %s: %s", ticker, expiry, exc)
        return None

    df = chain.calls if option_type.upper() == "CALL" else chain.puts

    row = df.loc[df["strike"] == strike]
    if row.empty:
        closest_idx = (df["strike"] - strike).abs().idxmin()
        row = df.loc[[closest_idx]]
        log.debug("exact strike %.2f not found, using %.2f",
                  strike, row.iloc[0]["strike"])

    r = row.iloc[0]
    bid = float(r.get("bid", 0) or 0)
    ask = float(r.get("ask", 0) or 0)

    if bid > 0 and ask > 0:
        return round((bid + ask) / 2, 4)
    last = float(r.get("lastPrice", 0) or 0)
    if last > 0:
        return round(last, 4)

    return None


def get_straddle_mid(ticker: str, expiry: str,
                     strike: float) -> Optional[float]:
    """
    Mid-price for an ATM straddle (call + put at same strike).
    Returns total per-share cost, or None.
    """
    call_mid = get_option_mid(ticker, expiry, strike, "CALL")
    put_mid = get_option_mid(ticker, expiry, strike, "PUT")
    if call_mid is not None and put_mid is not None:
        return round(call_mid + put_mid, 4)
    return None


def get_strangle_mid(ticker: str, expiry: str,
                     call_strike: float,
                     put_strike: float) -> Optional[float]:
    call_mid = get_option_mid(ticker, expiry, call_strike, "CALL")
    put_mid = get_option_mid(ticker, expiry, put_strike, "PUT")
    if call_mid is not None and put_mid is not None:
        return round(call_mid + put_mid, 4)
    return None


def get_strike_quote(ticker: str, expiry: str, strike: float, side: str) -> Optional[dict]:
    """
    Return quote/IV for the closest available strike on requested side.
    side: CALL or PUT
    """
    try:
        stock = yf.Ticker(ticker)
        chain = stock.option_chain(expiry)
    except Exception as exc:
        log.warning("strike quote fetch failed for %s %s: %s", ticker, expiry, exc)
        return None

    df = chain.calls if side.upper() == "CALL" else chain.puts
    if df.empty or "strike" not in df.columns:
        return None

    row = df.loc[df["strike"] == strike]
    if row.empty:
        closest_idx = (df["strike"] - strike).abs().idxmin()
        row = df.loc[[closest_idx]]

    r = row.iloc[0]
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
