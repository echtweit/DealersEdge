"""Quick scanner — run against the local API to analyze multiple tickers."""
import sys
import json
import urllib.request

TICKERS = sys.argv[1:] if len(sys.argv) > 1 else ["SPY", "TSLA", "NVDA", "META", "AAPL", "AMD"]

for tick in TICKERS:
    try:
        url = f"http://localhost:8000/api/dealer-map/{tick}"
        with urllib.request.urlopen(url, timeout=30) as resp:
            d = json.loads(resp.read())
    except Exception as e:
        print(f"ERROR fetching {tick}: {e}")
        continue

    dr = d["directional"]
    sa = d["straddle_analysis"]
    tc = dr.get("tech_context", {})
    vwap = d.get("technicals", {}).get("vwap", {})
    v20 = vwap.get("vwap_20d") or {}
    re = d["reynolds"]
    acf = d["acf_data"]
    ch = d["channel"]

    print("=" * 65)
    print(f"{d['ticker']}  ${d['spot']:.2f}  |  {d['dte']}d  {d['expiration']}")
    print(f"  Thesis:    {dr['thesis']} — {dr['bias']['action']}")
    print(f"  Direction: {dr['bias']['direction']} ({dr['bias']['strength']})")
    print(f"  Desc:      {dr['bias']['description']}")
    print(f"  GEX: {d['gex_regime']}  Re: {re['regime']} ({re['number']:.2f})  ACF: {acf['mean_acf1']:.3f}")
    ma_str = tc.get("ma_alignment", "?")
    rs_str = tc.get("rs_label", "?")
    vwap_str = f"${v20.get('value', 0):.2f}" if v20.get("value") else "—"
    print(f"  MA: {ma_str}  RS: {rs_str}  VWAP: {vwap.get('context', '?')} {vwap_str}")
    print(f"  Channel: ${ch.get('floor', 0):.2f}–${ch.get('ceiling', 0):.2f}")
    print()

    print("  POSITIONS:")
    for p in dr.get("positions", []):
        print(f"    {p['action']} {p['option_type']} @ ${p.get('strike', '?')}")
        print(f"      DTE: {p.get('dte_guidance', '')}  |  Target: {p.get('target', '')}  |  Stop: {p.get('stop', '')}")
        print(f"      Edge: {p.get('edge', '')}")
    print()

    print(f"  STRADDLE: {sa['verdict']} (score: {sa['score']['total']}/100)")
    cost = sa["straddle"]["total_cost"]
    be = sa["straddle"]["required_move_pct"]
    mp = sa["move_probability"]["probability"]
    print(f"    Cost: ${cost:.2f}  BE: {be}%  Move Prob ({d['dte']}d): {mp}%")
    best = d.get("expiry_scan", {}).get("best")
    if best:
        print(f"    Best expiry: {best['expiration']} ({best['dte']}d)  Score: {best['score']}")
    print()
