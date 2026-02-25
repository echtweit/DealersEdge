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

# Cron templates (informational — user installs manually)
CRON_TEMPLATE = """\
# PaperTrader cron jobs (adjust paths to your environment)
# Scan for new signals at 10:00 AM ET (15:00 UTC), Mon-Fri
0 15 * * 1-5  cd {project_dir} && python -m papertrader scan

# Check open positions 3x/day during market hours
30 15 * * 1-5  cd {project_dir} && python -m papertrader check   # 10:30 AM ET — post-open
0 18 * * 1-5   cd {project_dir} && python -m papertrader check   # 1:00 PM ET — midday
45 20 * * 1-5  cd {project_dir} && python -m papertrader check   # 3:45 PM ET — pre-close
"""
