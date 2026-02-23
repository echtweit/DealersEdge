"""
Options chain data fetching via yfinance.
Provides raw OI, greeks, and IV data for dealer analysis.
"""
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional


def get_expirations(ticker: str, min_dte: int = 0, max_dte: int = 60) -> list[dict]:
    stock = yf.Ticker(ticker)
    today = datetime.now().date()
    results = []
    for exp_str in stock.options:
        exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        dte = (exp_date - today).days
        if min_dte <= dte <= max_dte:
            results.append({"date": exp_str, "dte": dte})
    return results


def get_options_chain(ticker: str, expiration: str) -> dict:
    stock = yf.Ticker(ticker)
    chain = stock.option_chain(expiration)

    info = stock.fast_info
    current_price = info.get("lastPrice") or info.get("previousClose", 0)

    calls_df = chain.calls.copy()
    puts_df = chain.puts.copy()

    for df in [calls_df, puts_df]:
        for col in ["openInterest", "volume"]:
            if col in df.columns:
                df[col] = df[col].fillna(0).astype(int)
        for col in ["impliedVolatility", "lastPrice", "bid", "ask"]:
            if col in df.columns:
                df[col] = df[col].fillna(0).astype(float)

    calls = calls_df.to_dict("records")
    puts = puts_df.to_dict("records")

    return {
        "ticker": ticker,
        "expiration": expiration,
        "current_price": current_price,
        "calls": calls,
        "puts": puts,
    }


def get_price_history(ticker: str, period: str = "3mo", interval: str = "1d") -> list[dict]:
    stock = yf.Ticker(ticker)
    df = stock.history(period=period, interval=interval)
    if df.empty:
        return []

    records = []
    for ts, row in df.iterrows():
        records.append({
            "date": ts.strftime("%Y-%m-%d"),
            "open": round(float(row["Open"]), 2),
            "high": round(float(row["High"]), 2),
            "low": round(float(row["Low"]), 2),
            "close": round(float(row["Close"]), 2),
            "volume": int(row["Volume"]),
        })
    return records


def get_spot_price(ticker: str) -> float:
    stock = yf.Ticker(ticker)
    info = stock.fast_info
    return info.get("lastPrice") or info.get("previousClose", 0)


def get_ticker_info(ticker: str) -> dict:
    stock = yf.Ticker(ticker)
    info = stock.fast_info
    try:
        full_info = stock.info
        name = full_info.get("shortName", ticker)
    except Exception:
        name = ticker

    return {
        "ticker": ticker,
        "name": name,
        "price": info.get("lastPrice") or info.get("previousClose", 0),
        "market_cap": getattr(info, "market_cap", None),
    }
