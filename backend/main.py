"""
Dealer's Edge — Options Trading Tool API
Exploiting mechanical dealer hedging flows for directional option buying.
"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import traceback

from options_data import get_expirations, get_options_chain, get_spot_price, get_ticker_info, get_price_history
from gex_calculator import calculate_gex_profile, calculate_aggregate_gex
from max_pain import calculate_max_pain, find_oi_walls
from acf_engine import scan_ticker_acf
from gamma_reynolds import compute_gamma_reynolds, detect_phase_transition
from gamma_channel import extract_channel, channel_strategy
from directional_engine import classify_thesis
from straddle_analyzer import analyze_straddles
from technicals import compute_technicals
from collision_time import compute_collision_times
from vol_analysis import compute_vol_analysis
from models import DealerMapResponse

app = FastAPI(title="Dealer's Edge", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.get("/api/ticker/{ticker}")
def ticker_info(ticker: str):
    try:
        return get_ticker_info(ticker.upper())
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not fetch ticker info: {e}")


@app.get("/api/expirations/{ticker}")
def expirations(ticker: str, min_dte: int = 0, max_dte: int = 60):
    try:
        return get_expirations(ticker.upper(), min_dte, max_dte)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not fetch expirations: {e}")


@app.get("/api/price-history/{ticker}")
def price_history(ticker: str, period: str = "3mo", interval: str = "1d"):
    try:
        return get_price_history(ticker.upper(), period, interval)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not fetch price history: {e}")


@app.get("/api/aggregate-gex/{ticker}")
def aggregate_gex(ticker: str, max_dte: int = 45):
    ticker = ticker.upper()
    try:
        return calculate_aggregate_gex(ticker, max_dte)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Aggregate GEX failed: {str(e)}")


@app.get("/api/dealer-map/{ticker}", response_model=DealerMapResponse)
def dealer_map(
    ticker: str,
    expiration: str = None,
    min_dte: int = 0,
    max_dte: int = 60,
    account_size: float = None,
):
    """
    Full dealer positioning map + directional thesis.
    Integrates GEX, ACF regime, Gamma Reynolds, gamma channel,
    and generates buy-only directional guidance.
    """
    ticker = ticker.upper()

    try:
        # 1. Options chain
        selected_exp = expiration
        if not selected_exp:
            exps = get_expirations(ticker, min_dte, max_dte)
            if not exps:
                exps = get_expirations(ticker, 0, 60)
            if not exps:
                raise HTTPException(status_code=404, detail="No expirations found")
            selected_exp = exps[0]["date"]

        chain = get_options_chain(ticker, selected_exp)
        spot = chain["current_price"]
        exp_date = datetime.strptime(selected_exp, "%Y-%m-%d").date()
        dte = (exp_date - datetime.now().date()).days

        # 2. GEX profile
        gex = calculate_gex_profile(chain["calls"], chain["puts"], spot, dte)

        # 3. Max pain + OI walls
        pain = calculate_max_pain(chain["calls"], chain["puts"])
        walls = find_oi_walls(chain["calls"], chain["puts"], spot)

        # 4. ACF regime (from intraday price data)
        acf = scan_ticker_acf(ticker, period="5d", interval="2m")
        if acf.get("status") != "OK":
            acf = {
                "status": "FALLBACK",
                "regime": "NEUTRAL",
                "mean_acf1": 0,
                "pct_dampened": 0,
                "pct_amplified": 0,
                "stability": "UNKNOWN",
                "acf_trend": "UNKNOWN",
                "daily_results": [],
            }

        # 5. Gamma Reynolds
        reynolds = compute_gamma_reynolds(chain["calls"], chain["puts"], spot)

        # 6. Phase transition
        phase = detect_phase_transition(acf.get("daily_results", []))

        # 7. Gamma Channel
        channel = extract_channel(gex["gex_by_strike"], spot)
        ch_strat = channel_strategy(
            channel, gex["regime"], reynolds["regime"], reynolds["reynolds_number"]
        )

        # 8. Price history + Technicals (needed before directional thesis)

        price_hist = []
        try:
            price_hist = get_price_history(ticker, period="1y", interval="1d")
        except Exception:
            pass

        technicals = compute_technicals(ticker, price_hist)

        # 8b. Inject GEX entropy into technicals so directional engine can access it
        technicals["_gex_entropy"] = gex.get("entropy", {})

        # 8c. Compute IV/HV + skew (needs chain + price history, NOT multi-exp yet)
        from vol_analysis import _compute_iv_vs_hv, _compute_skew
        closes_for_vol = [bar["close"] for bar in price_hist if bar.get("close", 0) > 0]
        _ivhv = _compute_iv_vs_hv(reynolds.get("atm_iv", 0.3), closes_for_vol, dte)
        _skew = _compute_skew(chain["calls"], chain["puts"], spot, dte)
        _ratio = _ivhv.get("iv_hv_ratio", 1.0)
        _vrp_ctx = "HIGH_PREMIUM" if _ratio > 1.6 else "MODERATE_PREMIUM" if _ratio > 1.3 else \
                   "SMALL_PREMIUM" if _ratio > 1.1 else "FAIR" if _ratio > 0.9 else "DISCOUNT"
        technicals["_vol_context"] = {
            "iv_hv_ratio": _ratio,
            "iv_context": _ivhv.get("context", "FAIR"),
            "skew_regime": _skew.get("regime", "UNKNOWN"),
            "vrp_context": _vrp_ctx,
        }

        # 9. Directional thesis + positions + level actions
        directional = classify_thesis(
            spot=spot,
            acf=acf,
            reynolds=reynolds,
            phase=phase,
            gex_regime=gex["regime"],
            channel=channel,
            channel_strat=ch_strat,
            max_pain=pain["max_pain"],
            call_wall=walls["call_wall"],
            put_wall=walls["put_wall"],
            flip_point=gex["flip_point"] or spot,
            abs_gamma_strike=gex["abs_gamma_strike"],
            total_charm=gex["total_charm"],
            total_vanna=gex["total_vanna"],
            dte=dte,
            technicals=technicals,
            total_gex=gex["total_gex"],
            account_size=account_size,
        )

        # 10. Key levels (needed for straddle P/L scenarios)
        key_levels = {
            "max_pain": pain["max_pain"],
            "call_wall": walls["call_wall"],
            "put_wall": walls["put_wall"],
            "top_call_walls": walls["top_call_walls"],
            "top_put_walls": walls["top_put_walls"],
            "flip_point": gex["flip_point"],
            "abs_gamma_strike": gex["abs_gamma_strike"],
        }

        # 11. Vol Analysis (IV/HV, term structure, skew, VRP) — computed before straddles
        multi_exp_chains = []
        try:
            near_exps = get_expirations(ticker, 2, 45)[:4]
            for exp_info in near_exps:
                if exp_info["date"] == selected_exp:
                    multi_exp_chains.append({
                        "dte": dte,
                        "expiration": selected_exp,
                        "calls": chain["calls"],
                        "puts": chain["puts"],
                    })
                else:
                    try:
                        exp_chain = get_options_chain(ticker, exp_info["date"])
                        multi_exp_chains.append({
                            "dte": exp_info["dte"],
                            "expiration": exp_info["date"],
                            "calls": exp_chain["calls"],
                            "puts": exp_chain["puts"],
                        })
                    except Exception:
                        pass
        except Exception:
            pass

        vol_analysis = compute_vol_analysis(
            calls=chain["calls"],
            puts=chain["puts"],
            spot=spot,
            dte=dte,
            price_history=price_hist,
            atm_iv=reynolds.get("atm_iv", 0.3),
            multi_exp_chains=multi_exp_chains if multi_exp_chains else None,
            gex_regime=gex["regime"],
            total_gex=gex["total_gex"],
            reynolds_regime=reynolds["regime"],
        )

        # 12. Straddle/Strangle analysis (uses VRP from vol_analysis)
        straddle_analysis = analyze_straddles(
            calls=chain["calls"],
            puts=chain["puts"],
            spot=spot,
            dte=dte,
            acf=acf,
            reynolds=reynolds,
            phase=phase,
            gex_regime=gex["regime"],
            channel=channel,
            price_history=price_hist,
            technicals=technicals,
            key_levels=key_levels,
            vrp_data=vol_analysis.get("vrp"),
            account_size=account_size,
        )

        atr_dollar = technicals.get("atr", {}).get("atr", 0)
        collision_levels = {
            "call_wall": walls["call_wall"]["strike"],
            "put_wall": walls["put_wall"]["strike"],
            "max_pain": pain["max_pain"],
            "flip_point": gex["flip_point"],
            "abs_gamma_strike": gex["abs_gamma_strike"],
            "channel_floor": channel.get("floor"),
            "channel_ceiling": channel.get("ceiling"),
        }
        collision_times = compute_collision_times(
            spot, collision_levels, atr_dollar,
            acf.get("regime", "NEUTRAL"), reynolds["regime"], dte,
        )

        # 13. Multi-expiration straddle scan
        expiry_scan = _scan_expirations_for_straddles(
            ticker, spot, acf, reynolds, phase, gex["regime"],
            price_hist, technicals, selected_exp,
        )

        distance_map = {}
        for label, val in [
            ("max_pain", pain["max_pain"]),
            ("call_wall", walls["call_wall"]["strike"]),
            ("put_wall", walls["put_wall"]["strike"]),
            ("flip_point", gex["flip_point"]),
            ("abs_gamma_strike", gex["abs_gamma_strike"]),
        ]:
            if val and val > 0:
                distance_map[label] = {
                    "value": val,
                    "distance": round(abs(spot - val), 2),
                    "distance_pct": round(abs(spot - val) / spot * 100, 2),
                    "side": "above" if val > spot else "below",
                }

        available_expirations = get_expirations(ticker, 0, 60)

        return {
            "ticker": ticker,
            "spot": spot,
            "expiration": selected_exp,
            "dte": dte,
            "timestamp": datetime.now().isoformat(),

            # GEX regime
            "gex_regime": gex["regime"],
            "gex_regime_label": "Positive Gamma — Dealers Stabilizing" if gex["regime"] == "POSITIVE_GAMMA"
                                else "Negative Gamma — Dealers Amplifying",

            # ACF regime (from price action)
            "acf_regime": acf.get("regime", "NEUTRAL"),
            "acf_data": {
                "mean_acf1": acf.get("mean_acf1", 0),
                "pct_dampened": acf.get("pct_dampened", 0),
                "pct_amplified": acf.get("pct_amplified", 0),
                "stability": acf.get("stability", "UNKNOWN"),
                "trend": acf.get("acf_trend", "UNKNOWN"),
                "n_days": acf.get("n_days", 0),
                "at_squeeze_ceiling": acf.get("at_squeeze_ceiling", False),
                "self_excitation": acf.get("self_excitation", {}),
            },

            # Gamma Reynolds (with beta adjustment)
            "reynolds": {
                "number": reynolds["reynolds_number"],
                "number_beta_adj": directional.get("tech_context", {}).get("re_beta_adj", reynolds["reynolds_number"]),
                "beta_adj_factor": directional.get("tech_context", {}).get("beta_adj_factor", 1.0),
                "regime": reynolds["regime"],
                "speculative_gamma": reynolds["speculative_gamma"],
                "dealer_gamma": reynolds["dealer_gamma"],
                "call_put_ratio": reynolds["call_put_ratio"],
                "atm_iv": reynolds["atm_iv"],
            },

            # Phase transition
            "phase": {
                "regime": phase.get("regime", "LAMINAR"),
                "pct_amplified": phase.get("pct_amplified", 0),
                "distance_to_transition": phase.get("distance_to_transition", 0),
                "warning": phase.get("warning"),
            },

            # Gamma Channel
            "channel": channel,
            "channel_strategy": ch_strat,

            # Directional thesis (the main output)
            "directional": directional,

            # Straddle/Strangle analysis
            "straddle_analysis": straddle_analysis,

            # Multi-expiration straddle scan
            "expiry_scan": expiry_scan,

            # Collision times (Kanazawa first-passage-time)
            "collision_times": collision_times,

            # Volatility analysis (IV/HV, term structure, skew)
            "vol_analysis": vol_analysis,

            # Structural technicals
            "technicals": technicals,

            # Raw data
            "key_levels": key_levels,
            "distances": distance_map,
            "gex_profile": {
                "total_gex": gex["total_gex"],
                "total_call_gex": gex["total_call_gex"],
                "total_put_gex": gex["total_put_gex"],
                "total_charm": gex["total_charm"],
                "total_vanna": gex["total_vanna"],
                "by_strike": gex["gex_by_strike"],
                "entropy": gex.get("entropy", {}),
            },
            "max_pain_profile": {
                "max_pain": pain["max_pain"],
                "by_strike": pain["pain_by_strike"],
            },
            "available_expirations": available_expirations,
        }

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


def _scan_expirations_for_straddles(
    ticker, spot, acf, reynolds, phase, gex_regime,
    price_hist, technicals, current_exp,
):
    """
    Scan 4-6 near-term expirations to find the best DTE for a straddle.
    Returns a ranked list with scores and the recommended expiration.
    """
    try:
        all_exps = get_expirations(ticker, 2, 45)
    except Exception:
        return {"best": None, "expirations": []}

    if not all_exps:
        return {"best": None, "expirations": []}

    # Pick up to 6 spread across the range
    candidates = all_exps[:6]
    results = []

    for exp_info in candidates:
        exp_date = exp_info["date"]
        exp_dte = exp_info["dte"]
        try:
            chain = get_options_chain(ticker, exp_date)
            from straddle_analyzer import _build_straddle, _compute_iv_vs_rv
            straddle = _build_straddle(chain["calls"], chain["puts"], spot)
            re_for_exp = reynolds
            atm_iv = float(straddle.get("call_iv", 0) + straddle.get("put_iv", 0)) / 200
            if atm_iv <= 0:
                atm_iv = reynolds.get("atm_iv", 0.3)
            iv_rv = _compute_iv_vs_rv(atm_iv, price_hist, exp_dte)

            atr_pct = (technicals or {}).get("atr", {}).get("atr_pct", 0)
            be_pct = straddle.get("required_move_pct", 99)
            atr_coverage = round(atr_pct / be_pct, 2) if be_pct > 0 and atr_pct > 0 else 0

            # Simple score: IV value + ATR coverage + DTE sweet spot
            iv_score = max(0, 30 - iv_rv["iv_rv_ratio"] * 20)
            atr_score = min(30, atr_coverage * 20)
            # Prefer 7-14 DTE sweet spot (enough time, not too much theta)
            dte_score = 20 if 7 <= exp_dte <= 14 else 15 if 5 <= exp_dte <= 21 else 5
            total_score = round(iv_score + atr_score + dte_score)

            results.append({
                "expiration": exp_date,
                "dte": exp_dte,
                "cost": straddle.get("total_cost", 0),
                "cost_per_contract": straddle.get("total_cost_per_contract", 0),
                "breakeven_pct": be_pct,
                "iv": round(atm_iv * 100, 1),
                "iv_rv_ratio": iv_rv["iv_rv_ratio"],
                "atr_coverage": atr_coverage,
                "score": total_score,
                "is_current": exp_date == current_exp,
            })
        except Exception:
            continue

    results.sort(key=lambda r: r["score"], reverse=True)
    best = results[0] if results else None

    return {
        "best": best,
        "expirations": results,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
