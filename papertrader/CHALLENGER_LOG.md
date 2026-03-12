# Challenger Trader Change Log

This file is the single source of truth for what changes are active in the challenger trader versus the frozen baseline.

## Baseline Snapshot (Frozen)

- Baseline ID: `baseline_v1`
- Freeze date: `2026-02-27` (NY)
- Intent: keep this rule-set unchanged while challenger variants are tested.
- Observed snapshot at freeze:
  - Closed trades: `163`
  - Total closed P&L: `$1,756`
  - Win rate: `34.9%`
  - Avg P&L: `-11.6%`
  - Biggest issue: stop-outs dominate loss contribution.

## How to Use This Log

- Add one entry per challenger version (`challenger_v1`, `challenger_v2`, ...).
- Keep entries append-only (do not rewrite historical conclusions).
- For each entry, record:
  - exact rule/config deltas
  - hypothesis and expected effect
  - activation date/time
  - deactivation/rollback criteria
  - observed results and final decision

## Entry Template

```md
## challenger_vX - YYYY-MM-DD

- Status: planned | active | paused | retired | promoted
- Owner: <name>
- Based on baseline: baseline_v1

### Change Set
- <rule/config change #1>
- <rule/config change #2>

### Hypothesis
- <what should improve and why>

### Activation
- Start: <timestamp + timezone>
- End (if any): <timestamp + timezone>
- Scope: <tickers / hours / full run>

### Guardrails
- Max drawdown guard: <value>
- Minimum sample before decision: <N closed trades>
- Abort conditions: <conditions>

### Metrics to Compare (vs baseline_v1)
- Win rate delta:
- Avg P&L delta:
- Stop rate delta:
- Total P&L delta:
- Max drawdown delta:

### Result
- Outcome: pass | fail | inconclusive
- Notes:
- Decision: keep | rollback | iterate
```

## Planned Next Entry

## challenger_v1 - 2026-03-02

- Status: implemented (inactive until `PT_CHALLENGER_V1=1`)
- Based on baseline: `baseline_v1`

### Change Set

- Enable optional dynamic exits:
  - `PT_EXIT_ON_SIGNAL_FLIP=1`
  - `PT_EXIT_ON_REGIME_BREAK=1`
  - `PT_EXIT_ON_IV_CONFIRMATION_LOSS=1`
- Directional entry gating:
  - Require `confidence >= HIGH` for `MOMENTUM_BREAKOUT` entries.
  - Require `dte >= 2` for directional entries, unless confidence is `HIGH`.
- Directional stop tuning:
  - For entries not IV-confirmed by proxy, cap stop loss at `45%` (`PT_CH_V1_UNCONFIRMED_STOP_PCT`).
  - Keep parsed stop value when confidence proxy is `HIGH`.
- Note on IV confirmation at entry:
  - Current API payload does not expose a direct entry-time IV-confirmation field.
  - `challenger_v1` uses `confidence == HIGH` as a conservative proxy until a direct field is available.

### Hypothesis

- Reduce large downside tails from stale thesis/regime alignment.
- Lower stop-loss frequency and improve loss severity profile.

### Activation

- Start: pending (next week open)
- Scope: full watchlist execution layer
- Env flags:
  - `PT_CHALLENGER_V1=1`
  - `PT_EXIT_ON_SIGNAL_FLIP=1`
  - `PT_EXIT_ON_REGIME_BREAK=1`
  - `PT_EXIT_ON_IV_CONFIRMATION_LOSS=1`

## challenger_v2 - 2026-03-03

- Status: implemented (inactive until `PT_CHALLENGER_V2=1`)
- Based on baseline: `baseline_v1`
- Compares against: `challenger_v1`

### Change Set

- Inherits dynamic exits + directional controls from v1, with stricter tuning.
- Directional gate changes:
  - Raise minimum directional DTE to `3` (unless confidence is `HIGH`).
  - Block thesis buckets listed in `PT_CH_V2_BLOCK_THESES` (default: `NEUTRAL`).
  - For `MOMENTUM_BREAKOUT`, require `edge_type=WITH_DEALER` when `PT_CH_V2_BREAKOUT_REQUIRE_WITH_DEALER=1`.
- Stop tuning:
  - Tighten unconfirmed-proxy stop cap from `45%` (v1) to `40%` (v2).

### Hypothesis

- Further reduce stop-driven downside tails.
- Remove low-quality neutral directional entries.
- Keep breakout participation only when structural alignment is strongest.

### Activation

- Start: pending
- Scope: full watchlist execution layer in a separate DB (`papertrader_challenger_v2.db`)
- Env flags:
  - `PT_CHALLENGER_V2=1`
  - `PT_EXIT_ON_SIGNAL_FLIP=1`
  - `PT_EXIT_ON_REGIME_BREAK=1`
  - `PT_EXIT_ON_IV_CONFIRMATION_LOSS=1`

## challenger_v3 - 2026-03-12

- Status: implemented (inactive until `PT_CHALLENGER_V3=1`)
- Based on baseline: `baseline_v1`
- Compares against: `challenger_v1`, `challenger_v2`
- Motivation: data-driven insights from 399 closed trades across baseline/v1/v2

### Change Set

- **Block AGAINST_DEALER entries** (`PT_CH_V3_BLOCK_AGAINST_DEALER=1`):
  - AGAINST_DEALER directional trades averaged -60% in baseline, -19% in v1.
  - Override allowed only when confidence is HIGH **and** Reynolds regime is LAMINAR.
- **Reynolds gate** (`PT_CH_V3_MAX_REYNOLDS=1.0`):
  - Low Reynolds (< 0.7) was the only profitable tercile (+13% v1, +41% v2).
  - Block all directional entries when Reynolds > 1.0, unless WITH_DEALER breakout at HIGH confidence.
- **Optional POSITIVE_GAMMA | LAMINAR requirement** (`PT_CH_V3_REQUIRE_PG_LAMINAR=0`):
  - Disabled by default (set to 1 to enable strictest mode).
  - Only this regime combo was consistently profitable across all profiles.
- **Trimmed watchlist** (`PT_CH_V3_BLOCKED_TICKERS=AMZN,AAPL,NVDA,SPY,DASH`):
  - These mega-cap/high-liquidity names showed -49% to -66% avg P&L in baseline.
  - Dealer-flow mechanical edge is likely arbitraged away on these names.
- **Block NEUTRAL thesis** (`PT_CH_V3_BLOCK_THESES=NEUTRAL`):
  - Inherited from v2; NEUTRAL entries were -29% baseline, -13% v1.
- **Skip straddles in TURBULENT** (`PT_CH_V3_SKIP_STRADDLE_TURBULENT=1`):
  - TURBULENT straddles lost money across all profiles; skip when Reynolds regime is TURBULENT.
- **Tighter unconfirmed stop** (`PT_CH_V3_UNCONFIRMED_STOP_PCT=35`):
  - Reduced from 40% (v2) / 45% (v1) to limit bleed on unconfirmed entries.
- **Minimum DTE 3** (`PT_CH_V3_MIN_DIRECTIONAL_DTE=3`):
  - Carried from v2; 0-2 DTE entries underperformed 3-5 DTE across profiles.
- **Mandatory dynamic exits** (all three enabled in env):
  - SIGNAL_FLIP was the best exit in v1 (avg +9.88%), validated as must-have.
  - REGIME_BREAK roughly breakeven (capital preservation).
  - CONFIRMATION_LOSS moderate but useful for cutting stale positions.

### Hypothesis

- Dramatically reduce the trade count by filtering out historically destructive conditions.
- Concentrate entries in the only profitable regime/edge combinations.
- Expect fewer trades but materially better per-trade expectancy.
- Key metric: avg P&L per trade should be positive; win rate should exceed 30%.

### Activation

- Start: pending (next trading session)
- Scope: reduced watchlist (13 tickers after excluding AMZN, AAPL, NVDA, SPY, DASH)
- DB: `papertrader_challenger_v3.db`
- Env flags:
  - `PT_CHALLENGER_V3=1`
  - `PT_EXIT_ON_SIGNAL_FLIP=1`
  - `PT_EXIT_ON_REGIME_BREAK=1`
  - `PT_EXIT_ON_IV_CONFIRMATION_LOSS=1`

### Guardrails

- Min sample before decision: 50 closed trades
- Abort conditions: if avg P&L < -20% after 30+ trades, pause and re-evaluate

