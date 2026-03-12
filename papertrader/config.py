"""
Configuration for PaperTrader.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("PT_DB_PATH", str(BASE_DIR / "papertrader.db"))

API_BASE = os.environ.get("PT_API_BASE", "http://localhost:8000/api")

DEFAULT_WATCHLIST = [
    # Broad market ETFs
    "SPY", "QQQ", "IWM",
    # Mega-cap tech (high liquidity, tight spreads)
    "AAPL", "NVDA", "TSLA", "AMZN",
    # Large-cap non-tech (sector diversity)
    "JPM", "XOM", "UNH", "HD",
    # Mid-cap growth (higher vol, wider spreads)
    "CRWD", "DASH", "COIN", "MARA",
    # Mid-cap value / cyclical
    "UAL", "FSLR", "CLF",
]

# Exit-rule defaults (used when parsing from position text fails)
DEFAULT_STOP_LOSS_PCT = 50.0
DEFAULT_MAX_HOLD_DAYS = 14
STRADDLE_EXIT_BUFFER_DAYS = 2  # close straddle this many days before expiry

# Account size for contract sizing (overridable via CLI --account-size)
DEFAULT_ACCOUNT_SIZE = None

# Pricing/API handling (reduce rate-limit risk while keeping granularity)
CHAIN_CACHE_TTL_SEC = int(os.environ.get("PT_CHAIN_CACHE_TTL_SEC", "180"))
CHAIN_STALE_GRACE_SEC = int(os.environ.get("PT_CHAIN_STALE_GRACE_SEC", "900"))
CHAIN_FETCH_MAX_PER_MIN = int(os.environ.get("PT_CHAIN_FETCH_MAX_PER_MIN", "45"))
DAILY_CLOSE_CACHE_TTL_SEC = int(os.environ.get("PT_DAILY_CLOSE_CACHE_TTL_SEC", "900"))
DAILY_CLOSE_FETCH_MAX_PER_MIN = int(os.environ.get("PT_DAILY_CLOSE_FETCH_MAX_PER_MIN", "30"))

# Optional dynamic exits (all disabled by default).
# Set to "1" next week when you want to turn them on.
EXIT_ON_SIGNAL_FLIP = os.environ.get("PT_EXIT_ON_SIGNAL_FLIP", "0") == "1"
EXIT_ON_REGIME_BREAK = os.environ.get("PT_EXIT_ON_REGIME_BREAK", "0") == "1"
EXIT_ON_IV_CONFIRMATION_LOSS = os.environ.get("PT_EXIT_ON_IV_CONFIRMATION_LOSS", "0") == "1"

# Challenger profile (disabled by default; baseline remains unchanged).
CHALLENGER_V1_ENABLED = os.environ.get("PT_CHALLENGER_V1", "0") == "1"
CHALLENGER_MIN_DIRECTIONAL_DTE = int(os.environ.get("PT_CH_V1_MIN_DIRECTIONAL_DTE", "2"))
CHALLENGER_BREAKOUT_MIN_CONFIDENCE = os.environ.get("PT_CH_V1_BREAKOUT_MIN_CONFIDENCE", "HIGH").upper()
CHALLENGER_DTE_BYPASS_MIN_CONFIDENCE = os.environ.get("PT_CH_V1_DTE_BYPASS_MIN_CONFIDENCE", "HIGH").upper()
CHALLENGER_UNCONFIRMED_STOP_PCT = float(os.environ.get("PT_CH_V1_UNCONFIRMED_STOP_PCT", "45"))

# Challenger v2 (separate from v1; disabled by default).
# If both v1 and v2 are enabled, scanner prioritizes v2.
CHALLENGER_V2_ENABLED = os.environ.get("PT_CHALLENGER_V2", "0") == "1"
CH_V2_MIN_DIRECTIONAL_DTE = int(os.environ.get("PT_CH_V2_MIN_DIRECTIONAL_DTE", "3"))
CH_V2_BREAKOUT_MIN_CONFIDENCE = os.environ.get("PT_CH_V2_BREAKOUT_MIN_CONFIDENCE", "HIGH").upper()
CH_V2_DTE_BYPASS_MIN_CONFIDENCE = os.environ.get("PT_CH_V2_DTE_BYPASS_MIN_CONFIDENCE", "HIGH").upper()
CH_V2_UNCONFIRMED_STOP_PCT = float(os.environ.get("PT_CH_V2_UNCONFIRMED_STOP_PCT", "40"))
CH_V2_BREAKOUT_REQUIRE_WITH_DEALER = os.environ.get("PT_CH_V2_BREAKOUT_REQUIRE_WITH_DEALER", "1") == "1"
CH_V2_BLOCK_THESES = os.environ.get("PT_CH_V2_BLOCK_THESES", "NEUTRAL").upper()

# Challenger v3 — data-driven regime gating (disabled by default).
# If v3 is enabled it takes priority over v1/v2.
CHALLENGER_V3_ENABLED = os.environ.get("PT_CHALLENGER_V3", "0") == "1"
CH_V3_BLOCK_AGAINST_DEALER = os.environ.get("PT_CH_V3_BLOCK_AGAINST_DEALER", "1") == "1"
CH_V3_AGAINST_DEALER_OVERRIDE_MIN_CONF = os.environ.get("PT_CH_V3_AD_OVERRIDE_CONF", "HIGH").upper()
CH_V3_MAX_REYNOLDS_DIRECTIONAL = float(os.environ.get("PT_CH_V3_MAX_REYNOLDS", "1.0"))
CH_V3_REQUIRE_POS_GAMMA_LAMINAR = os.environ.get("PT_CH_V3_REQUIRE_PG_LAMINAR", "0") == "1"
CH_V3_MIN_DIRECTIONAL_DTE = int(os.environ.get("PT_CH_V3_MIN_DIRECTIONAL_DTE", "3"))
CH_V3_DTE_BYPASS_MIN_CONFIDENCE = os.environ.get("PT_CH_V3_DTE_BYPASS_CONF", "HIGH").upper()
CH_V3_UNCONFIRMED_STOP_PCT = float(os.environ.get("PT_CH_V3_UNCONFIRMED_STOP_PCT", "35"))
CH_V3_BLOCK_THESES = os.environ.get("PT_CH_V3_BLOCK_THESES", "NEUTRAL").upper()
CH_V3_BLOCKED_TICKERS = {
    t.strip().upper()
    for t in os.environ.get("PT_CH_V3_BLOCKED_TICKERS", "AMZN,AAPL,NVDA,SPY,DASH").split(",")
    if t.strip()
}
CH_V3_SKIP_STRADDLE_TURBULENT = os.environ.get("PT_CH_V3_SKIP_STRADDLE_TURBULENT", "1") == "1"

# Cron templates (informational — user installs manually)
CRON_TEMPLATE = """\
# PaperTrader cron jobs (adjust paths to your environment)
# Scan for new signals at 10:00 AM ET (15:00 UTC), Mon-Fri
0 15 * * 1-5  cd {project_dir} && python3 -m papertrader ensure-backend && python3 -m papertrader scan

# Optional dynamic exits (enable next week by setting these to 1)
# PT_EXIT_ON_SIGNAL_FLIP=1 PT_EXIT_ON_REGIME_BREAK=1 PT_EXIT_ON_IV_CONFIRMATION_LOSS=1
# Optional backend guard controls:
# PT_BACKEND_AUTOSTART=1 PT_BACKEND_START_WAIT_SEC=25 PT_BACKEND_BOOT_POLL_SEC=1
#
# Check open positions 3x/day during market hours
30 15 * * 1-5  cd {project_dir} && python3 -m papertrader check   # 10:30 AM ET — post-open
0 18 * * 1-5   cd {project_dir} && python3 -m papertrader check   # 1:00 PM ET — midday
45 20 * * 1-5  cd {project_dir} && python3 -m papertrader check   # 3:45 PM ET — pre-close
"""
