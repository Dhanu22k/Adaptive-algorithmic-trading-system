"""
config.py — Central configuration for all ORB trading system scripts
──────────────────────────────────────────────────────────────────────
Single import for credentials + settings. All pipeline files use:
    from config import CREDENTIALS, SETTINGS

Credentials are loaded from .env (never hardcoded, never committed).
Settings (cache path, risk params, instruments) are loaded from
settings.yaml (v1.4 spec Section 4: "nothing is hardcoded" -- Step 7).

Nothing downstream changed: SETTINGS is still the same Settings dataclass
with the same attribute names as before. core/*.py and run_*.py files
need zero changes for this migration.
"""

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import List

import yaml
from dotenv import load_dotenv

# ── Locate project root, .env, and settings.yaml ───────────────────────────────
_PROJECT_ROOT   = Path(__file__).parent.resolve()
_ENV_FILE       = _PROJECT_ROOT / ".env"
_SETTINGS_FILE  = _PROJECT_ROOT / "settings.yaml"

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
    Central settings for the ORB strategy system. Values are loaded from
    settings.yaml (see Settings.from_yaml below) -- the defaults declared
    here only kick in if a key is MISSING from the YAML file, so a partial
    settings.yaml never crashes the whole system, it just silently falls
    back per-field. Add new knobs here (with a sensible default) as each
    step is built, then add the matching key to settings.yaml.
    """

    # ── Data pipeline (Step 1) ─────────────────────────────────────────────────
    cache_dir:       str   = "data_cache"
    history_days:    int   = 30            # lookback window for historical fetch
    interval:        str   = "FIVE_MINUTE" # Angel One interval code
    max_age_hours:   int   = 20            # cache TTL — refresh after market close

    # ── Session feature engineer (Step 2) ─────────────────────────────────────
    or_end_bar:      int   = 3             # bars to define opening range (3 x 5min = 15min) -- v1.4 spec
    breakout_buffer_pct: float = 0.0015    # A2: close must clear OR_H by this % (v1.4 spec)
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
    atr_stop_mult:   float = 1.5           # stop = ATR x 1.5
    atr_target_mult: float = 2.5           # target = ATR x 2.5
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

    @classmethod
    def from_yaml(cls, yaml_path: Path) -> "Settings":
        """
        Loads settings.yaml (a nested dict, grouped into sections like
        data_pipeline/session_features/etc. for readability) and flattens
        it into this dataclass's fields. Any field NOT found in the YAML
        silently keeps its dataclass default above -- missing/partial
        settings.yaml never crashes the system, unlike missing .env values
        (which DO hard-fail, since those are genuine secrets with no safe
        default).
        """
        if not yaml_path.exists():
            print(f"[Config] ⚠ {yaml_path.name} not found — using built-in defaults for all settings.")
            return cls()

        with open(yaml_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        # Flatten: settings.yaml groups keys into sections for readability
        # (data_pipeline, session_features, ...) but Settings itself is flat.
        flat: dict = {}
        for key, value in raw.items():
            if isinstance(value, dict):
                flat.update(value)      # a section — merge its keys up
            else:
                flat[key] = value       # a top-level key (e.g. instruments)

        valid_field_names = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in flat.items() if k in valid_field_names}

        unknown = set(flat) - valid_field_names
        if unknown:
            print(f"[Config] ⚠ {yaml_path.name} has unrecognized key(s), ignored: {sorted(unknown)}")

        return cls(**kwargs)


SETTINGS = Settings.from_yaml(_SETTINGS_FILE)
print(f"[Config] Settings loaded from {_SETTINGS_FILE.name}")