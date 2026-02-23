# Dealer's Edge — Options Intelligence Tool

A dealer-aware options trading tool for 5–20 DTE plays. Maps the dealer's mechanical hedging obligations and identifies exploitable trade setups.

## What It Does

- **Dealer Positioning Map**: Calculates GEX (Gamma Exposure) profile, flip point, max pain, call/put walls for any ticker
- **Regime Classification**: Identifies whether dealers are stabilizing (positive gamma) or amplifying (negative gamma) price action
- **Setup Detection**: Automatically classifies conditions into four setup types:
  - **Pin Trade** — Sell premium when dealers are pinning near max pain
  - **Wall Fade** — Fade price into dealer-defended call/put walls
  - **GEX Flip Breakout** — Buy options when the flip point breaks and dealers start amplifying
  - **Vanna/Charm Drift** — Ride post-IV-event drift toward high OI clusters
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

Backend runs on http://localhost:8000

### 2. Frontend (Vite)

```bash
cd frontend
npm install
npm run dev
```

Frontend runs on http://localhost:3000 (proxies API calls to backend)

### 3. Use It

Open http://localhost:3000, type a ticker (SPY, QQQ, AAPL, etc.), and hit Analyze.

## Data Sources

All data is free — no API keys required:

- **yfinance**: Options chain data (OI, greeks, IV, all strikes/expirations)
- **Calculated locally**: GEX, max pain, flip point, charm, vanna all computed from the raw chain

## Architecture

```
backend/
  main.py                FastAPI app — orchestrates all modules into /api/dealer-map
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
Charm:  ∂Δ/∂t — measures how delta decays as time passes (pin drift)
Vanna:  ∂Δ/∂σ — measures how delta shifts when IV changes (vol-driven drift)
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
- **Call/Put Walls** — strikes with the highest open interest concentration. In positive
  gamma regimes these act as magnets; in negative gamma they become breakout accelerators.
- **Gamma Channel** — the floor and ceiling of the high-gamma zone, defining the
  expected trading range for the expiration.

**References:**
- Barbon & Buraschi, ["Gamma Fragility"](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3725454) (2021) — formalizes how dealer hedging
  creates mechanical supply/demand at gamma-heavy strikes.

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
- Analogy drawn from Navier-Stokes turbulence theory. The critical Reynolds number
  concept (laminar→turbulent transition) maps directly to the dealer capacity question.
- Avellaneda & Stoikov, ["High-frequency trading in a limit order book"](https://www.tandfonline.com/doi/abs/10.1080/14697680701381228)
  (Quantitative Finance 8(3), 2008) — market-maker inventory dynamics under flow pressure.

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
- Sornette, ["Why Stock Markets Crash"](https://press.princeton.edu/books/paperback/9780691175959/why-stock-markets-crash) (Princeton, 2003) — critical phenomena and
  log-periodic oscillations as precursors to phase transitions in markets.
- Scheffer et al., ["Early-warning signals for critical transitions"](https://doi.org/10.1038/nature08227)
  (Nature, 2009) — entropy-based early warning signals for regime shifts in complex systems.

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
- Kanazawa, Kim & Kanazawa, ["Exact solution of the two-body financial dealer
  model"](https://arxiv.org/abs/2205.15558) (arXiv:2205.15558, 2022) — derives mean collision
  time between interacting financial agents using kinetic theory.
- Ducournau, ["Stock Market Physical Properties via Stokes' Law"](https://arxiv.org/abs/2103.00721)
  (arXiv:2103.00721, 2021) — odds-ratio refinement of collision probability
  for price reaching key levels.

---

### 7. Volatility Analysis (IV/HV, Term Structure, Skew, VRP)

**What it measures:** Whether options are cheap or expensive, whether you should
buy naked or use spreads, and the structural headwind from variance risk premium.

Four sub-analyses answer pre-entry questions:

**IV vs. HV:** ATM implied volatility divided by DTE-matched realized volatility
(annualized standard deviation of log-returns). Ratio > 1.3 = expensive,
< 0.9 = cheap.

**Term Structure:** ATM IV slope across expirations. Contango (front < back)
is normal. Backwardation (front > back) signals near-term event risk or fear.

**Put/Call Skew:** `Skew = OTM_put_IV − OTM_call_IV`. HIGH_PUT_SKEW means
downside protection is expensive (fear). CALL_SKEW means upside is being bid
(unusual, often bullish signal).

**Variance Risk Premium (VRP):** The structural gap between implied and realized
variance, adjusted for dealer positioning:

```
VRP = (IV² − HV²) × 100
VRP_adj = IV² − (HV × GEX_mult)²
```

Dealers dampen realized vol by 15–25% when long gamma (mult 0.78–0.88) and
amplify it by 8–15% when short gamma (mult 1.08–1.15). This adjustment reveals
the *true* VRP after accounting for the dealer's stabilizing/destabilizing effect.

**References:**
- Bakshi & Kapadia, ["Delta-Hedged Gains and the Negative Market Volatility Risk
  Premium"](https://doi.org/10.1093/rfs/hhg002) (Review of Financial Studies, 2003) — establishes
  that straddles have a structurally negative risk premium (~−10% weeklies, ~−19%
  monthlies). The "Dark Matter" of options.
- Bühler, Gonon, Teichmann & Wood, ["Deep hedging"](https://doi.org/10.1080/14697688.2019.1571683)
  (Quantitative Finance, 2019) — framework for GEX-implied volatility adjustment.

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
portfolio, further scaled by IV/HV ratio and VRP context to avoid over-allocating
when options are expensive.

**Wall-break probability:** Starts at a 15% base and is adjusted by beta-adjusted
Reynolds, gamma asymmetry (positive gamma dampens ~2.5× more effectively than
negative gamma amplifies, per Bakshi), ACF trend, phase proximity, self-excitation,
GEX entropy, and Ducournau collision probability.
