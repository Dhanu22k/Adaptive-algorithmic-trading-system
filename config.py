"""
config.py — Central configuration for all ORB trading system scripts
──────────────────────────────────────────────────────────────────────
Single import for credentials + settings. All pipeline files use:
    from config import CREDENTIALS, SETTINGS

Credentials are loaded from .env (never hardcoded).
Settings (cache path, risk params, instruments) live here.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv

# ── Locate project root and .env ───────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.resolve()
_ENV_FILE     = _PROJECT_ROOT / ".env"

if not _ENV_FILE.exists():
    raise FileNotFoundError(
        f"\n[Config] .env not found at: {_ENV_FILE}\n"
        f"\n  Setup steps:\n"
        f"  1. Copy the template:  Copy-Item .env.example .env\n"
        f"  2. Open .env and fill in your real Angel One credentials.\n"
        f"  3. Re-run the script.\n"
    )

load_dotenv(_ENV_FILE, override=False)   # OS env vars take priority over .env

# ── Validate and expose credentials ───────────────────────────────────────────
_REQUIRED_ENV_VARS = {
    "api_key"    : "ANGEL_API_KEY",
    "client_id"  : "ANGEL_CLIENT_ID",
    "mpin"       : "ANGEL_MPIN",
    "totp_secret": "ANGEL_TOTP_SECRET",
}

CREDENTIALS: dict = {key: os.getenv(env_var) for key, env_var in _REQUIRED_ENV_VARS.items()}

_missing = [env_var for key, env_var in _REQUIRED_ENV_VARS.items() if not CREDENTIALS[key]]
if _missing:
    raise EnvironmentError(
        f"\n[Config] Missing values in {_ENV_FILE.name}:\n"
        + "".join(f"  {v} = (empty)\n" for v in _missing)
        + f"\n  Open .env and fill in the missing values.\n"
    )

print(f"[Config] Credentials loaded from .env  (client: {CREDENTIALS['client_id']})")


# ── Application settings ───────────────────────────────────────────────────────
@dataclass
class Settings:
    """
    Central settings for the ORB strategy system.
    Add new knobs here as each step is built — keeps all params in one place.
    """

    # ── Data pipeline (Step 1) ─────────────────────────────────────────────────
    cache_dir:       str   = "data_cache"
    history_days:    int   = 30            # lookback window for historical fetch
    interval:        str   = "FIVE_MINUTE" # Angel One interval code
    max_age_hours:   int   = 20            # cache TTL — refresh after market close

    # ── Session feature engineer (Step 2) ─────────────────────────────────────
    or_end_bar:      int   = 6             # bars to define opening range (6 x 5min = 30min)
    entry_cutoff:    str   = "14:30"       # no new entries after this IST time
    squareoff_time:  str   = "15:15"       # hard square-off IST time

    # ── Phase A / B signal params (Step 3) ────────────────────────────────────
    rvol_threshold:  float = 1.5           # min RVOL for Phase A setup bar
    close_pos_min:   float = 0.65          # min close position within bar for Phase A
    max_pullback_bars: int = 5             # hard cutoff: cancel setup after 5 bars
    score_threshold: int   = 3             # Phase B min score (out of 8)

    # ── Risk management (Step 4) ───────────────────────────────────────────────
    capital:          float = 100000.0     # Rs 1L validation-phase capital
    risk_per_trade_pct: float = 0.01       # 1% of capital risked per trade (Rs 1,000)
                                            # -> 2 stopped-out trades = exactly the daily limit below
    atr_stop_mult:   float = 1.5           # stop = ATR × 1.5
    atr_target_mult: float = 2.5           # target = ATR × 2.5
    min_gross_move:  float = 0.006         # skip trade if ATR < 0.6% (too small)
    daily_loss_limit: float = 2000.0       # Rs 2,000 daily stop (2% of Rs 1L capital)
    max_position_pct: float = 0.25         # max 25% of capital in one position
    trail_trigger_pct: float = 0.50        # move stop to breakeven+costs at 50% of target reached

    # ── Transaction costs (Step 4) ─────────────────────────────────────────────
    txn_cost_normal_pct: float = 0.0027    # ~Rs 66 round trip on target/squareoff exits
    txn_cost_stop_pct:   float = 0.0033    # ~Rs 80 round trip on stop exits (extra slippage)

    # ── Instruments ────────────────────────────────────────────────────────────
    instruments: List[str] = field(
        default_factory=lambda: ["HDFCBANK", "ICICIBANK", "RELIANCE", "INFY", "TCS"]
    )


SETTINGS = Settings()