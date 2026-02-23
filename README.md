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
  main.py              FastAPI app with /api/dealer-map endpoint
  options_data.py      yfinance data fetching
  gex_calculator.py    GEX, charm, vanna calculations (Black-Scholes)
  max_pain.py          Max pain + OI wall detection
  setup_classifier.py  Setup classification engine

frontend/
  index.html           Dashboard UI
  src/main.js          Rendering logic + charts
  src/style.css        Dark trading dashboard theme
  src/utils/           API client + formatters
```
