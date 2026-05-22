# Dealer's Edge — Options Intelligence Tool

A dealer-aware options trading tool for 5–20 DTE plays. Maps the dealer's mechanical hedging obligations and identifies exploitable trade setups.

> Educational side project only — this is not a professional trading system and is not intended for live trading or investment advice.  
> The models and signals are currently being validated through forward testing.

> **Open interest caveat:** Dealer GEX, walls, max pain, and flip levels are built from **open interest (OI)**. This repo pulls OI via **yfinance**, which reflects **end-of-day** exchange reporting — not continual **intraday** OI updates. During the session, large trades roll the dealer book while the map still shows yesterday's positioning. **Without a live or frequently refreshed OI feed, this is not a great tool for intraday dealer-flow trading** — treat outputs as a rough prior on where hedging *was*, not a real-time map of where dealers *are* now.

## What It Does

- **Dealer Positioning Map**: Calculates GEX (Gamma Exposure) profile, flip point, max pain, call/put walls for any ticker
- **Regime Classification**: Identifies whether dealers are stabilizing (positive gamma) or amplifying (negative gamma) price action
- **Setup Detection**: Automatically classifies conditions into four setup types:
  - **Pin Trade** — In **positive gamma** (long-gamma hedging): sell premium when **mechanical pinning** (hedge-toward-strike + charm) favors **compression** toward max pain / high-GEX — *not* the same as “price sitting near a strike,” and **not** the story when **negative gamma** dominates (amplification, not pin)
  - **Wall Fade** — Fade price into dealer-defended call/put walls
  - **GEX Flip Breakout** — Buy options when the flip point breaks and dealers start amplifying
  - **Vanna/Charm Drift** — Ride post-IV-event **drift** from greeks toward large OI clusters (contrast with strict **pinning**, which needs long-gamma dominance)
- **Trade Guidance**: Structure recommendations, greek targets, exit rules, and risk management
- **Visual Dashboard**: GEX profile charts, OI distribution, decision tree, key levels

## Quick Start

### 1. Backend (Python)

```bash
cd backend
python -m venv venv
source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Backend runs on http://localhost:8000 by default. If you run the API on another port (for example **8010** to match a personal `commands.txt` snippet), set the same port everywhere: **`DE_API_PORT`** (or run uvicorn manually), **`VITE_API_PROXY_TARGET`** for the Vite dev proxy, and **`PT_API_BASE`** for PaperTrader / cron (`http://localhost:PORT/api`). See repo-root **`.env.example`**.

### 2. Frontend (Vite)

```bash
cd frontend
npm install
npm run dev
```

Frontend runs on http://localhost:5173 (proxies API calls to backend)

### 3. Use It

Open http://localhost:5173, type a ticker (SPY, QQQ, AAPL, etc.), and hit Analyze.

## Continuous integration

On pushes and pull requests to `main` or `master`, [GitHub Actions](.github/workflows/ci.yml) installs `backend/requirements.txt`, runs **pytest**, then runs **`npm ci`** and **`npm run build`** in `frontend/`.

## Paper Trader (Forward Testing)

A cron-driven forward-testing framework that auto-enters all DealersEdge signals, tracks actual option P&L via yfinance, and stores full signal snapshots for attribution analysis.

```bash
# Ensure backend is running (auto-starts if down), then:

python -m papertrader ensure-backend

# Scan default watchlist for new signals
python -m papertrader scan

# Scan specific tickers with account size
python -m papertrader --account-size 10000 scan SPY QQQ TSLA

# Check open positions for exit conditions (target/stop/expiry)
python -m papertrader check

# View open positions
python -m papertrader status

# Performance report
python -m papertrader report

# Signal attribution — which signals actually predict well?
python -m papertrader analyze

# Closed trade log
python -m papertrader history --limit 100

# Print crontab snippet for automated scheduling
python -m papertrader cron
```

### Scan liquidity & hedge pressure (optional)

Newer dealer-map payloads include **`hedge_context`**: an Amihud-style **liquidity** proxy (stored scaled as **liquidity_proxy**, interpret as ×1e6 in UI copy) and **hedge_pressure_proxy**. The PaperTrader scanner writes these to `scans` when the API returns them.

- **Environment:** `PT_BAR_SOURCE` — `auto` (Polygon when `POLYGON_API_KEY` / `MASSIVE_API_KEY` is set), `polygon`, or `yf` (yfinance only). Details in `.env.example`.
- **Backfill:** `python -m papertrader backfill-liquidity-hedge --help` — fills missing columns from embedded `full_response.hedge_context` or recomputes from daily bars through the scan calendar day. For a full historical refresh after metric or bar-source changes, use pacing flags and e.g. `--all-profiles --force --recompute-always` (slow); `PT_BAR_SOURCE=yf` avoids Polygon rate limits on large runs. Recomputed history uses bars only through each scan’s calendar date (no lookahead); numbers may differ slightly from what an older live dealer-map response stored for that day (window length, vendor data revisions).
- **Where it shows up:** dealer-map strip (live); `insights`, `significance-report`, `calendar-report`, and `generalized-report` include scan Amihud/HPP quartile strata when the DB columns are populated. For interaction tests: `alignment-report --prespec-liquidity-regime` (bundled GEX×liquidity cells) and `fingerprint-corr --by-profile-liquidity` when comparing profiles on the same machine.
- **Explicit cohort filters:** `cohort-eval` applies JSON AND-rules on fingerprint dimensions (same pooled Amihud/HPP quartiles as alignment / fingerprint-corr) and prints baseline vs filtered **n, win rate, P&L% and dollars**. Example: `python -m papertrader cohort-eval --baseline-only --rules papertrader/cohort_rules_example.json`. Details: `papertrader/analysis/README.md`.

### Statistical Analysis (Charts)

Generate a separate chart pack from papertrader databases:

```bash
python analysis/stat_analysis.py

# Optional: custom lookback and profile DBs
python analysis/stat_analysis.py --days 90 \
  --db baseline=papertrader/papertrader_baseline.db \
  --db challenger_v1=papertrader/papertrader_challenger_v1.db
```

Outputs are written to `analysis/output/<timestamp>/` as PNG charts, CSVs, and `summary.md`.

### Dealer Behavior Analysis (Raw Signals)

Analyze raw scan snapshots (independent of trading execution) to infer regime behavior:

```bash
python analysis/dealer_behavior_analysis.py

# Optional: custom lookback and profile DBs
python analysis/dealer_behavior_analysis.py --days 30 \
  --db baseline=papertrader/papertrader_baseline.db \
  --db challenger_v1=papertrader/papertrader_challenger_v1.db
```

Outputs are written to `analysis/output/<timestamp>/dealer_behavior/` and include:
- Regime return heatmap (`gex_regime x reynolds_regime`)
- Regime transition matrix
- Wall interaction bounce/break panel
- OPEX-conditioned behavior chart
- `summary_behavior.md`

### How It Works

1. **Scan** calls `/api/dealer-map/{ticker}` for each ticker, stores the full response, and opens paper trades for every actionable signal (directional positions + straddles/strangles).
2. **Monitor** periodically fetches live option prices and checks exit conditions: underlying hits target, option premium drops past stop-loss %, DTE reaches 0, or max hold time exceeded.
3. **Reporter** queries the SQLite trade journal to compute win rates, P&L, Sharpe ratio, and breaks down performance by regime (GEX, Reynolds, ACF), thesis type, confidence level, and VRP — so you can see exactly which signals hold up in practice.

Every trade stores the complete signal snapshot at entry time (regime labels, Kelly sizing, wall-break probability, entropy, etc.) enabling rigorous attribution analysis.

For experiment governance, track all challenger-rule changes in:
`papertrader/CHALLENGER_LOG.md`

### Cron Schedule

```
0 15 * * 1-5        ensure-backend + scan at 10:00 AM ET (Mon-Fri)
*/30 14-21 * * 1-5   Check exits every 30 min during market hours
```

## Data Sources

Most data is free — no API keys required for the default path:

- **yfinance**: Options chain data (OI, greeks, IV, all strikes/expirations). **OI is the limiting factor:** it updates once per session (after the prior close is published), not tick-by-tick or continuously through the day. Volume and IV refresh more often; **dealer positioning metrics do not** unless you plug in a vendor that supplies intraday OI.
- **Calculated locally**: GEX, max pain, flip point, charm, vanna all computed from the raw chain — so they inherit whatever staleness is in the OI snapshot.

If you need this to be useful for same-day hedging-flow reads, plan on a paid options data feed with **intraday OI** (or at minimum multiple refreshes per session) and wire it into `options_data.py` in place of yfinance's chain.

## Architecture

```
backend/
  main.py                FastAPI app — orchestrates all modules into /api/dealer-map
  liquidity_hedge_metrics.py  Scan-time Amihud proxy + hedge pressure (`hedge_context` on dealer-map)
  options_data.py        yfinance data fetching (chains, expirations, price history)
  gex_calculator.py      GEX profile, charm, vanna, entropy (Black-Scholes)
  gamma_reynolds.py      Gamma Reynolds Number + phase transition detection
  acf_engine.py          ACF regime detection + Hawkes self-excitation index
  collision_time.py      First-passage-time ETA to dealer levels (Kanazawa)
  directional_engine.py  Central thesis classifier — positions, levels, sizing
  vol_analysis.py        IV/HV, term structure, skew, VRP
  straddle_analyzer.py   Non-directional vol-buying setup scoring
  technicals.py          MAs, ATR, relative strength, beta, rolling VWAP
  max_pain.py            Max pain + OI wall detection
  scan.py                Multi-ticker CLI scanner

frontend/
  index.html             Dashboard UI
  src/main.js            Rendering logic + charts
  src/style.css          Dark trading dashboard theme
  src/utils/             API client + formatters

papertrader/
  __main__.py            CLI entry point (scan / check / status / report / analyze / history)
  config.py              API URL, default watchlist, cron templates, `PT_BAR_SOURCE`
  db.py                  SQLite schema + CRUD (scans incl. liquidity_proxy / hedge_pressure_proxy, trades, price_checks)
  backfill_liquidity_hedge.py  Backfill scan liquidity + HPP from JSON or bar history
  scanner.py             Calls DealersEdge API, parses exit rules, opens paper trades
  monitor.py             Checks open trades for target/stop/expiry/time exits
  pricing.py             Live option mid-prices via yfinance
  reporter.py            Performance analytics + signal attribution
```

---

## Technical Methodology

The tool layers six independent analytical modules — each grounded in a distinct
research tradition — into a single thesis classifier. No single signal drives the
output; the directional engine requires convergence across multiple frameworks
before issuing a position recommendation.

### 1. Dealer Gamma Exposure (GEX)

**What it measures:** The net gamma dealers hold at each strike, and therefore
the direction and magnitude of the hedging flows they are *mechanically obligated*
to execute.

Dealers (market makers) are structurally short options. When they sell a call, they
are short gamma — a $1 move in the underlying forces them to buy ~γ×OI×100 shares
to stay delta-neutral. This buying amplifies the move. When net gamma is positive
(dealers are long gamma from put sales), the opposite happens: they sell into rallies
and buy dips, *dampening* price action.

**Formulas:** Full Black-Scholes greeks computed locally from the raw options chain:

```
Gamma:  γ = φ(d₁) / (S · σ · √T)
Charm:  ∂Δ/∂t — delta decay into expiry; combines with **gamma sign**: with **long gamma**, helps **compress** toward strikes (pin mechanics); with **short gamma**, works the **opposite** way (amplification / “anti-pin” near large short strikes)
Vanna:  ∂Δ/∂σ — how delta shifts when IV changes (vol-driven drift)
```

Dealer GEX per strike:
```
Call GEX = +γ · OI · 100 · S      (dealers short calls → forced to buy on up-move)
Put GEX  = −γ · OI · 100 · S      (dealers short puts → forced to sell on down-move)
```

**Key outputs:**
- **GEX Flip Point** — the strike where net dealer gamma crosses zero; above it dealers
  dampen, below it they amplify. Computed via linear interpolation of the nearest
  zero-crossing to spot.
- **Call/Put Walls** — strikes with the highest open interest concentration. In **positive**
  gamma they often behave as **magnets** (stabilization); in **negative gamma** they tend to
  **amplify** breaks — avoid calling either “pinning” unless you mean **long-gamma** hedge-toward-strike flows.
- **Gamma Channel** — the floor and ceiling of the high-gamma zone, defining the
  expected trading range for the expiration.

**References:**
- Barbon & Buraschi, ["Gamma Fragility"](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3725454) (working paper; widely cited) — gamma imbalance, intraday dynamics, fragility. **Broader context:** [Research collection: dealer hedging flows](#research-collection-dealer-hedging-flows-literature).

---

### 2. Gamma Reynolds Number

**What it measures:** Whether speculative flow is overwhelming dealers' ability to
absorb it — analogous to the Reynolds number in fluid dynamics, which determines
whether flow is laminar (smooth) or turbulent (chaotic).

```
Re_γ = |Speculative Gamma| / |Dealer Gamma|
```

- **Speculative gamma:** Near-ATM call volume × gamma proxy × S² — represents
  fresh directional flow (primarily retail call-buying).
- **Dealer gamma:** Accumulated OI × gamma proxy × S² — represents the dealer's
  existing hedging book.
- **Gamma proxy:** A Gaussian moneyness weighting `exp(-0.5·(ln(S/K)/σ)²)` that
  concentrates the measurement on ATM strikes where gamma is highest.

| Re | Regime | Implication |
|----|--------|-------------|
| < 0.7 | LAMINAR | Dealers absorb flow comfortably. Walls hold, mean-reversion dominates. |
| 0.7–1.0 | TRANSITIONAL | Dealers straining. Walls may or may not hold. |
| > 1.0 | TURBULENT | Dealers overwhelmed. Walls become breakout accelerators, not barriers. |

**Beta adjustment:** Low-beta stocks are disproportionately vulnerable to gamma
squeezes — the same absolute dollar move represents a larger percentage move,
triggering more aggressive rebalancing. The Reynolds number is adjusted:
`Re_adj = Re_raw × (1 / max(β, 0.3))`.

**References:**
- Ducournau, ["Stock Market Physical Properties via Stokes' Law"](https://arxiv.org/abs/2103.00721)
  (arXiv:2103.00721, 2021) — proposes an econophysics Reynolds number for stock markets,
  classifying market dynamics as laminar, transitory, or turbulent based on supply/demand
  collision mechanics. Directly foundational to our Gamma Reynolds concept.
- The laminar→turbulent transition analogy is drawn from Navier-Stokes fluid dynamics.
  Our adaptation replaces Ducournau's volume-based inputs with gamma-weighted speculative
  flow vs. dealer inventory capacity.

---

### 3. ACF Regime Detection (Autocorrelation Function)

**What it measures:** Whether intraday returns are mean-reverting (negative
autocorrelation, dealers dampening) or trending (positive autocorrelation,
dealers amplifying).

The lag-1 autocorrelation of 2-minute intraday returns is computed for each of
the last 5 trading days:

```
ACF(1) = Cov(rₜ, rₜ₊₁) / Var(r)
```

Empirically across a 37-ticker panel, the average ACF(1) is approximately −0.20,
and ~93% of days show dampened (negative ACF) behavior. This is the "normal" state:
dealers are long gamma and mean-reverting price.

| ACF(1) | Regime | Meaning |
|--------|--------|---------|
| < −0.05 | LONG_GAMMA | Dealers dampening — fade moves |
| > +0.05 | SHORT_GAMMA | Dealers amplifying — ride momentum |
| else | NEUTRAL | No clear regime signal |

**Stability & trend:** The engine also tracks whether the ACF regime is STABLE,
DEEPENING (becoming more extreme), or REVERSING (regime change in progress) — and
whether the percentage of amplified days is approaching the critical phase transition
threshold (~12.9% amplified days historically marks the boundary).

**References:**
- Cont, ["Empirical properties of asset returns: stylized facts and statistical
  issues"](https://doi.org/10.1080/713665670) (2001) — foundational work on return autocorrelation structure.
- Bouchaud, Bonart, Donier & Gould, [*Trades, Quotes and Prices: Financial Markets
  Under the Microscope*](https://www.cambridge.org/us/universitypress/subjects/physics/econophysics-and-financial-physics/trades-quotes-and-prices-financial-markets-under-microscope)
  (Cambridge University Press, 2018, ISBN 9781107156050) — connects negative lag-1
  autocorrelation to market-maker mean-reversion activity.

---

### 4. Hawkes Self-Excitation Index

**What it measures:** Whether moves are *begetting more moves* — the hallmark of
self-exciting feedback loops where dealer hedging amplifies directional momentum.

While ACF measures autocorrelation at a fixed lag, the self-excitation index (SEI)
captures *clustering* of directional moves:

```
For each bar:
  If |return| > threshold AND same sign as previous qualifying move:
    Extend current cluster
  Else:
    Record cluster, start new one

SEI = mean(cluster_size × cluster_intensity) across all clusters
```

| SEI | Regime | Meaning |
|-----|--------|---------|
| > 150 | HIGH_EXCITATION | Strong feedback loops — moves amplify rapidly |
| > 80 | MODERATE | Occasional momentum bursts |
| > 40 | LOW | Weak self-excitation |
| ≤ 40 | NONE | Mean-reversion dominant |

**References:**
- Bacry, Mastromatteo & Muzy, ["Hawkes processes in finance"](https://doi.org/10.1142/S2382626615500057)
  (Market Microstructure and Liquidity, 2015) — Hawkes point processes for modelling
  self-exciting order flow.
- Hardiman, Bercot & Bouchaud, ["Critical reflexivity in financial markets"](https://doi.org/10.1140/epjb/e2013-40107-3)
  (Eur. Phys. J. B, 2013) — empirical evidence that markets operate near the critical
  Hawkes branching ratio n ≈ 1.

---

### 5. GEX Entropy — Phase Transition Early Warning

**What it measures:** How *concentrated* or *dispersed* dealer gamma is across
strikes. Borrowed from statistical mechanics, where entropy measures the number
of accessible microstates of a system.

Shannon entropy of the GEX distribution:

```
pᵢ = |net_gex_i| / Σ|net_gex_j|        # probability distribution
H  = −Σ pᵢ · ln(pᵢ)                     # Shannon entropy (nats)
H_norm = H / ln(N)                       # normalized to [0, 1]
```

| H_norm | Regime | Meaning |
|--------|--------|---------|
| < 0.3 | CRITICAL | Gamma clustered at 1–2 strikes — phase transition imminent |
| < 0.5 | APPROACHING | Significant clustering — elevated instability |
| < 0.7 | MODERATE | Some clustering, generally stable |
| ≥ 0.7 | DISPERSED | Evenly spread — stable equilibrium |

When entropy is CRITICAL and Reynolds is at least TRANSITIONAL, the directional
engine overrides to treat the regime as TURBULENT — the system is primed for a
violent move but hasn't triggered yet.

**References:**
- Scheffer et al., ["Early-warning signals for critical transitions"](https://doi.org/10.1038/nature08227)
  (Nature, 2009) — establishes that complex systems exhibit generic statistical warning
  signals (rising variance, critical slowing down) before abrupt regime shifts. Our entropy
  metric is a conceptual adaptation: where Scheffer measures variance amplification near
  tipping points, we measure gamma *concentration* as a proxy for system fragility.
- Sornette, ["Why Stock Markets Crash"](https://press.princeton.edu/books/paperback/9780691175959/why-stock-markets-crash)
  (Princeton, 2003) — broader framework for viewing market crashes as critical phase
  transitions with endogenous origins. Sornette's methodology (log-periodic power laws)
  differs from ours (Shannon entropy), but the shared insight is that proximity to a phase
  transition is detectable from the statistical structure of the system before the
  transition occurs.

---

### 6. Collision Time — First-Passage-Time ETA

**What it measures:** The expected number of trading days for price to reach each
key dealer level, using the first-passage-time formula from kinetic gas theory.

This replaces vague "X% away" distance measurements with a physics-derived ETA:

```
T_expected = L² / (2σ²)
```

Where L is the dollar distance to the level and σ is the daily ATR (dollar
volatility). This is the mean first-passage time for a random walk to reach a
barrier — the same formula used in molecular diffusion and Brownian motion.

Regime-adjusted arrival time:
```
TURBULENT:     T_adj = T_raw × 0.6    (dealers amplifying → moves faster)
TRANSITIONAL:  T_adj = T_raw × 0.8
LONG_GAMMA:    T_adj = T_raw × 1.4    (dealers dampening → moves slower)
```

The collision probability (chance of reaching the level within DTE) is computed
via the complementary error function:
```
P(T ≤ DTE) = erfc(L / (σ · √(2·DTE)))
```

**References:**
- The formula T = L²/(2σ²) is the classical mean first-passage time for one-dimensional
  Brownian motion reaching an absorbing barrier — a standard result in stochastic
  processes (see e.g. Redner, [*A Guide to First-Passage Processes*](https://doi.org/10.1017/CBO9780511606014),
  Cambridge, 2001).
- Kanazawa, Takayasu & Takayasu, ["Exact solution to two-body financial dealer
  model"](https://arxiv.org/abs/2205.15558) (J. Stat. Phys. 190, 2023) — extends first-passage
  analysis to interacting dealer agents using kinetic theory. Our regime-adjustment
  multipliers (turbulent=0.6×, dampened=1.4×) are an empirical adaptation of their
  insight that dealer interaction alters collision timescales.
- Ducournau, ["Stock Market Physical Properties via Stokes' Law"](https://arxiv.org/abs/2103.00721)
  (arXiv:2103.00721, 2021) — odds-ratio refinement of collision probability.

---

### 7. Volatility Analysis (IV/HV, Term Structure, Skew, VRP)

**What it measures:** Whether options are cheap or expensive, whether you should
buy naked or use spreads, and the relative richness/cheapness of implied vol.

Four sub-analyses answer pre-entry questions:

**IV vs. HV:** ATM implied volatility divided by DTE-matched realized volatility
(annualized standard deviation of log-returns). Ratio > 1.3 = expensive,
< 0.9 = cheap.

**Term Structure:** ATM IV slope across expirations. Contango (front < back)
is normal. Backwardation (front > back) signals near-term event risk or fear.

**Put/Call Skew:** `Skew = OTM_put_IV − OTM_call_IV`. HIGH_PUT_SKEW means
downside protection is expensive (fear). CALL_SKEW means upside is being bid
(unusual, often bullish signal).

**Variance Risk Premium (VRP):** The gap between implied and realized variance,
adjusted for dealer positioning:

```
VRP = (IV² − HV²) × 100
VRP_adj = IV² − (HV × GEX_mult)²
```

Dealers dampen realized vol by 15–25% when long gamma (mult 0.78–0.88) and
amplify it by 8–15% when short gamma (mult 1.08–1.15). This adjustment reveals
the true VRP after accounting for the dealer's stabilizing/destabilizing effect.

**Important update — structural VRP has weakened:** Bakshi & Kapadia (2003)
originally documented a consistently negative VRP (options systematically
overpriced). However, Dew-Becker & Giglio (Chicago Fed WP 2025-17) show that
equity index option alphas have become "indistinguishable from zero" over the
past 15 years, driven by declining intermediary frictions rather than changing
investor preferences. Our scoring reflects this: VRP is used as a *relative*
richness signal (is IV currently elevated vs. GEX-adjusted realized vol?), not
as a structural headwind assumption. The straddle analyzer's VRP penalty has
been halved from its original values.

**References:**
- Bakshi & Kapadia, ["Delta-Hedged Gains and the Negative Market Volatility Risk
  Premium"](https://doi.org/10.1093/rfs/hhg002) (Review of Financial Studies 16(2), 2003) —
  foundational paper documenting the negative VRP. Still valid as context for
  *why* selling vol was historically profitable, but the magnitude of the premium
  has largely disappeared per the Dew-Becker & Giglio finding below.
- Dew-Becker & Giglio, ["The Disappearing Index Option Premium"](https://www.chicagofed.org/publications/working-papers/2025/2025-17)
  (Chicago Fed Working Paper 2025-17, 2025) — shows index option alphas have
  converged to zero since ~2010, attributing the change to reduced intermediary
  frictions. This directly motivated our reduction of VRP penalty weights.
- The GEX-implied realized vol adjustment (dealers long gamma dampen realized vol by
  ~15–25%, short gamma amplifies by ~8–15%) is a practitioner heuristic derived from
  the Barbon & Buraschi "Gamma Fragility" finding that dealer gamma positioning
  mechanically alters realized volatility via forced hedging flows.

---

### 8. Directional Thesis Engine

**What it does:** Synthesizes all of the above into actionable positions. This is
a rule-based decision tree, not a black-box model — every output can be traced
to specific input conditions.

**Thesis classification flow:**

```
                    ┌─ Re_adj > 1.0 + ACF momentum ──→ MOMENTUM_BREAKOUT
                    │
                    ├─ Re_adj > 1.0 ──────────────────→ MOMENTUM_EARLY
                    │
Input signals ──────├─ pct_amp > 13% but Re < 0.7 ───→ CONFLICTED_PIN
(Re, ACF, GEX,     │
entropy, MAs,       ├─ ACF1 > 0.05 ──────────────────→ MOMENTUM_TREND
beta, RS, VWAP)     │
                    ├─ ACF1 < −0.10 (strong) ────────→ FADE_MOVES
                    │
                    ├─ ACF1 < −0.05 (mild) ──────────→ FADE_MILD
                    │
                    └─ else ──────────────────────────→ NEUTRAL
```

**Direction determination:** Combines GEX regime bias, MA alignment, relative
strength, VWAP position, and charm/vanna flow direction into a single BULLISH/
BEARISH/NEUTRAL output with strength (STRONG/MODERATE/WEAK).

**Position sizing:** Uses a modified half-Kelly criterion capped at 0.25–5% of
portfolio, further scaled by IV/HV ratio and VRP context. Per Dew-Becker &
Giglio (2025), the VRP scaling has been softened — IV/HV ratio is now the
primary sizing signal, with VRP providing a secondary adjustment.

**Wall-break probability:** Starts at a 15% base and is adjusted by beta-adjusted
Reynolds, gamma asymmetry (positive gamma dampens ~2.5× more effectively than
negative gamma amplifies, per Barbon & Buraschi), ACF trend, phase proximity, self-excitation,
GEX entropy, and Ducournau collision probability.

---

## Modern Validation & Caveats

Markets evolve. Several of our foundational references are 10–20+ years old.
Here is how we've validated (or revised) each against modern research:

| Concept | Original Source | Status | Modern Evidence |
|---|---|---|---|
| **Dealer gamma effects** | Barbon & Buraschi (2021) | **Confirmed** | Recent empirical work (Li & Todorov 2023, Healy 2024) continues to find that GEX predicts intraday vol dynamics. The mechanism (forced hedging) is structural and persists. |
| **Return autocorrelation** | Cont (2001) | **Confirmed** | "Stylized fact" — replicated across decades and asset classes. Autocorrelation structure is a statistical property of returns, not a regime that can arbitrage away. |
| **Hawkes self-excitation** | Hawkes (1971), Bacry et al. (2015) | **Confirmed** | Widely used in modern high-frequency trading, order-flow analysis, and risk management. Refinements exist but the core model is standard. |
| **Phase transitions / entropy** | Sornette (2003), Scheffer et al. (2009) | **Conceptually valid** | Our Shannon entropy approach is inspired by, not derived from, these works. The concept that concentrated positioning precedes regime shifts is well-supported by practitioner experience and recent event studies. |
| **Variance Risk Premium** | Bakshi & Kapadia (2003) | **Revised** | Dew-Becker & Giglio (Chicago Fed WP 2025-17) show index option alphas have converged to zero since ~2010. We've halved our VRP penalty weights and treat VRP as a relative richness signal, not a structural headwind. |
| **First-passage time** | Redner (2001), Kanazawa et al. (2018) | **Valid** | The Brownian motion first-passage formula is a mathematical identity. Kanazawa's dealer-interaction refinement is a theoretical extension; we use it conservatively. |
| **Kelly criterion** | Kelly (1956) | **Timeless** | Mathematical optimality result. We use half-Kelly with regime-based scaling — standard risk management practice. |
| **Avellaneda & Stoikov** | Avellaneda & Stoikov (2008) | **Confirmed** | Remains the foundational market-making model. Actively used and extended in academic and industry research (Guéant et al. 2013, Cartea et al. 2015). |
| **Market microstructure** | Bouchaud et al. (2018) | **Current** | Published 2018; Bouchaud's group at CFM continues active research. The book is a standard reference for quantitative trading programs. |

---

## Research collection: dealer hedging flows (literature)

This section is our **in-repo brief**: mechanisms academics emphasize, how they relate to **positive vs negative GEX regime** language in this tool, and **which papers to weight** when improving **truthiness** (not overclaiming) and **effectiveness** (testable predictions). Educational only — not investment advice.

### The economic setup (all papers share this skeleton)

1. **Listed options are in zero net supply.** End-users collectively hold a net position; **market makers** sit on the other side of the aggregate book (up to clearing/conventions).
2. **Delta neutrality is dynamic.** To remain roughly delta-neutral, MMs trade the **underlying** (or futures) when spot moves. The **size** of required hedging trades depends on **gamma** (how fast delta moves with spot).
3. **So spot feels an extra demand/supply channel** that is **mechanical** (risk limits + hedging rules), **state-dependent** (where spot is relative to strikes and time to expiry), and **liquidity-constrained** (impact is larger when the stock or future cannot absorb flow cheaply).

“Exploiting dealer hedging” in a disciplined sense means: **map that state-dependence**, not **assume intent** or a single magic level.

### Topic 1 — Gamma sign: dampening vs amplification (“pin mechanics” vs not)

**Intuition (first-order):**

- When the **dealer book is long gamma** (in aggregate / locally; our **positive GEX regime** is the operational proxy), delta-hedging tends to **sell into strength and buy weakness** → **stabilizing** path, and **together with charm** near expiry this is the rigorous sense in which people say **pinning / compression toward strikes**.
- When the book is **short gamma** (**negative GEX regime**), hedging tends to **buy into rallies and sell into selloffs** relative to the pre-move hedge → **feedback** that **amplifies** moves. This is **not** “pinning” in the same mechanical sense; social media often mislabels **price near a big strike** as a pin when the dominant effect is **short-gamma amplification** or **liquidity stress**.

**Papers that formalize this channel:**

| Paper | What it adds (deep point) |
|--------|---------------------------|
| **Barbon & Buraschi**, *Gamma Fragility* ([SSRN 3725454](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3725454)) | **Pre-trade gamma imbalance** in options interacts with **underlying illiquidity** to explain **intraday momentum vs reversal** and links to **fragility** (including crash-like dynamics). Takeaway: **sign of gamma** alone is incomplete — **liquidity** scales whether the spot market can absorb hedge flow without large impact. |
| **Anderegg, Ulmann & Sornette** (2022), *J. International Money and Finance* ([EconPapers](https://econpapers.repec.org/article/eeejimfin/v_3a124_3ay_3a2022_3ai_3ac_3as0261560622000304.html)) | Theory + empirics: **OMM gamma sign** is tied to **spot volatility** (short-gamma positioning ↔ higher volatility in their setting). Useful **academic cousin** to our “+GEX dampen / −GEX amplify” narrative — different market (FX) but same **hedging-friction** logic. |

**For this repo:** Strengthen truthiness by treating **regime + liquidity** as a pair in copy and, longer term, in **PaperTrader** diagnostics (e.g. signal strength conditional on a simple illiquidity proxy).

### Topic 2 — Who consumes underlying liquidity when?

**O’Donovan, Yu & Zhang** — *Option Market Maker Hedging and Stock Market Liquidity* ([SSRN 4567604](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4567604)):

- Uses data that **separates market-makers from other traders**.
- Core result (**intuition**): **MM net short** options → hedging **demands** liquidity from the stock (**destabilizing** stress possible); **net long** → hedging **supplies** stabilizing flow.
- **Deep point:** Same gamma story **bites harder** when **liquidity supply** in the equity is limited — aligns with Barbon–Buraschi’s emphasis on **interaction** with illiquidity, but in **equity** microstructure language.

**For this repo:** Justifies UI/educational text like **“negative gamma + thin liquidity = worst-case path risk”** without anthropomorphizing dealers.

### Topic 3 — How large can index / 0DTE hedging be?

**Amaya, Garcia-Ares, Pearson & Vasquez** — *0DTE Index Options and Market Volatility: How Large is Their Impact?* ([SSRN 5113405](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5113405); Cboe research circle):

- **Proprietary** information on **OMM positioning / gamma** in **SPX 0DTE**.
- **Function:** Quantify **upper bounds / realistic magnitude** of **volatility** effects from **hedge rebalancing**, not just sign stories.
- **Deep point:** Short-dated options have **large gamma per notional** → **rebalancing needs** can be big **intraday**; still not the same as “dealers control the close every day.”

**For this repo:** Use for **humility**: index-like, ultra-short-DTE days are where **magnitude** arguments are strongest; single-name mid-DTE may be **smaller** or **noisier**.

### Topic 4 — A surface-based “hedging pressure” factor (benchmark)

**Tang, Zhou & Zhou** — *Option Expected Hedging Demand* ([SSRN 4729672](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729672)):

- Builds **expected hedging demand (EHD)** from **real-time option data** (spot elasticity of delta, etc.).
- **Empirical claim:** EHD **predicts cross-sectional stock returns** over **several days**, then **reverses**.
- **Deep point:** Separates **mechanism** (everyone agrees MMs hedge) from **predictability** (must be **estimated**; can be **weak** or **unstable** post-publication).

**For this repo:** If you add **external validation**, EHD is a natural **benchmark** (“does our regime flag add information beyond EHD?”) using **PaperTrader** or offline research notebooks — not something to paste into production as alpha without testing.

### Topic 5 — Foundations: why customer demand moves option prices at all

**Garleanu, Pedersen & Poteshman** — *Demand-Based Option Pricing* ([*Review of Financial Studies* 2009](https://ideas.repec.org/a/oup/rfinst/v22y2009i10p4259-4299.html)):

- **Imperfect hedging** → **net demand** affects **option prices** and **cross-section / smile** (classic **index put** puzzle angle).
- **Deep point:** Large **customer** structures (collars, put spreads) are **boring buy-and-hold** from the fund side but still imply **risk absorption** by intermediaries — **not** “manipulation,” consistent with how we phrase **institutional** flows.

**For this repo:** Best for **README philosophy** and **explaining skew / richness**, less for **intraday GEX dashboard** tuning.

### Topic 6 — Microstructure / spreads (secondary for v1 product)

**Chang, Chung & Yu** (2014), *Journal of Financial Markets* — inventory, hedging costs, spreads ([EconPapers](http://econpapers.repec.org/RePEc:eee:finmar:v:18:y:2014:i:c:p:25-48)):

- **Function:** Explains **why** hedging is **costly** and **discrete** at the **market-maker** level.
- **For this repo:** Relevant if you later optimize **execution** or **per-name spread filters**; less central than **aggregate gamma-regime** papers above.

---

### Priority ladder (what to focus on, in order)

1. **Barbon–Buraschi + Anderegg–Ulmann–Sornette** — **Truthiness** for **gamma sign** language; **liquidity interaction**; avoids bogus “pin” claims under **short gamma**.
2. **O’Donovan–Yu–Zhang** — **Truthiness** for **spot liquidity stress** alongside regime.
3. **Amaya et al. (2025)** — **Truthiness** on **magnitude** for **index / 0DTE**; calibrates hype.
4. **Tang–Zhou–Zhou (EHD)** — **Effectiveness**: optional **out-of-model benchmark** for predictive audits.
5. **Garleanu–Pedersen–Poteshman** — **Foundations** / education; **skew and demand**, not intraday plumbing.
6. **Chang–Chung–Yu** — **Execution** / micro; **later**.

**Bottom line (same as our working stance):** use **(gamma regime × liquidity horizon)** as the spine; use **Amaya-type** evidence to **right-size** index claims; use **EHD-style** factors only as **benchmarks** until **PaperTrader** (or research code) proves incremental value.
