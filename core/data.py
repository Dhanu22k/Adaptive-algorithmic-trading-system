"""
Angel One SmartAPI — Data Pipeline
====================================
Step 1 of the Intraday NSE ORB Strategy implementation order.

Responsibilities (per Architecture v1.4):
  - Authenticate with Angel One SmartAPI
  - Fetch historical 5-minute OHLCV data
  - Normalize timestamps to bar-close, IST timezone
  - Detect and handle missing bars (W22)
  - Compute RVOL_ToD with expanding window (no leakage — W18)
  - Fetch concurrent sector index data alongside stock data (W19)
  - Save data locally so re-runs don't hit the API repeatedly
  - Provide a clean, validated DataFrame to all downstream components

Usage:
    from angel_data_pipeline import AngelDataPipeline, InstrumentConfig

    config = InstrumentConfig.HDFCBANK()
    pipeline = AngelDataPipeline(
        api_key="YOUR_KEY",
        client_id="YOUR_ID",
        mpin="YOUR_PIN",
        totp_secret="YOUR_SECRET"
    )
    df = pipeline.fetch(config, days=365)
    print(df.head())
"""

from __future__ import annotations

import json
import os
import time
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pyotp
import requests

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# INSTRUMENT CONFIG
# ---------------------------------------------------------------------------
@dataclass
class InstrumentConfig:
    """All NSE-specific facts for one instrument.
    Sector index is used for relative strength and Phase A invalidation.
    Symbol token is Angel One's internal identifier for the instrument.
    """
    ticker:             str
    symbol_token:       str    # Angel One instrument token
    exchange:           str    # "NSE" or "BSE"
    sector_index:       str    # e.g. "^NSEBANK" for banking stocks
    sector_token:       str    # Angel One token for the sector index
    sector_exchange:    str    # exchange for sector: "NSE" for index, "NFO" for futures proxy
    gap_threshold:      float  # gap % above which = Regime 2 (Wide Gap)
    name:               str    # human-readable name

    # Pre-built configs for the 5 recommended instruments
    @classmethod
    def HDFCBANK(cls) -> "InstrumentConfig":
        return cls(
            ticker="HDFCBANK",
            symbol_token="1333",
            exchange="NSE",
            sector_index="BANKNIFTY",
            sector_token="",        # auto-resolved to nearest FUTIDX by pipeline
            sector_exchange="NFO",  # BANKNIFTY futures live on NFO, not NSE
            gap_threshold=0.008,
            name="HDFC Bank"
        )

    @classmethod
    def ICICIBANK(cls) -> "InstrumentConfig":
        return cls(
            ticker="ICICIBANK",
            symbol_token="4963",
            exchange="NSE",
            sector_index="BANKNIFTY",
            sector_token="",        # auto-resolved to nearest FUTIDX by pipeline
            sector_exchange="NFO",  # BANKNIFTY futures live on NFO, not NSE
            gap_threshold=0.008,
            name="ICICI Bank"
        )

    @classmethod
    def RELIANCE(cls) -> "InstrumentConfig":
        return cls(
            ticker="RELIANCE",
            symbol_token="2885",
            exchange="NSE",
            sector_index="NIFTY",
            sector_token="",        # auto-resolved to nearest FUTIDX by pipeline
            sector_exchange="NFO",  # NIFTY futures live on NFO, not NSE
            gap_threshold=0.008,
            name="Reliance Industries"
        )

    @classmethod
    def INFY(cls) -> "InstrumentConfig":
        return cls(
            ticker="INFY",
            symbol_token="1594",
            exchange="NSE",
            # NOTE: "CNXIT" was the old index name; Angel One's ScripMaster
            # likely uses "NIFTYIT" for the current Nifty IT F&O contract.
            # UNVERIFIED — if get_active_futures_token() raises RuntimeError
            # on first run, that confirms the name doesn't match; the error
            # message will say so clearly rather than silently returning 0
            # candles (same diagnostic pattern used to find the BANKNIFTY bug).
            sector_index="NIFTYIT",
            sector_token="",        # auto-resolved to nearest FUTIDX by pipeline
            sector_exchange="NFO",
            gap_threshold=0.008,
            name="Infosys"
        )

    @classmethod
    def TCS(cls) -> "InstrumentConfig":
        return cls(
            ticker="TCS",
            symbol_token="11536",
            exchange="NSE",
            sector_index="NIFTYIT",  # see NOTE in INFY() above — unverified name
            sector_token="",        # auto-resolved to nearest FUTIDX by pipeline
            sector_exchange="NFO",
            gap_threshold=0.008,
            name="TCS"
        )

    @classmethod
    def from_ticker(cls, ticker: str) -> "InstrumentConfig":
        """Look up a pre-built config by ticker string, e.g. for iterating
        SETTINGS.instruments (a list of strings) without a manual if/elif chain."""
        builders = {
            "HDFCBANK":  cls.HDFCBANK,
            "ICICIBANK": cls.ICICIBANK,
            "RELIANCE":  cls.RELIANCE,
            "INFY":      cls.INFY,
            "TCS":       cls.TCS,
        }
        if ticker not in builders:
            raise ValueError(
                f"No pre-built InstrumentConfig for '{ticker}'. "
                f"Available: {list(builders.keys())}"
            )
        return builders[ticker]()


# ---------------------------------------------------------------------------
# FUTIDX SECTOR PROXY AUTO-RESOLVER
# ---------------------------------------------------------------------------
# Angel One API returns 0 candles for index tokens (e.g. 26009 for BANKNIFTY,
# 26000 for NIFTY, 26007 for CNXIT — all NSE index symbols, not just
# BANKNIFTY). Fix: use the nearest-expiry FUTIDX contract on NFO as a sector
# proxy for ANY underlying index. Futures track the index within 0.01%.
# This function auto-picks the nearest expiry so no manual rollover is needed.

_SCRIP_MASTER_URL = (
    "https://margincalculator.angelbroking.com"
    "/OpenAPI_File/files/OpenAPIScripMaster.json"
)

_MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
    "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
    "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _parse_angel_expiry(expiry_str: str) -> date:
    """Convert Angel One expiry string '28JUL2026' → date(2026, 7, 28)."""
    day   = int(expiry_str[:2])
    month = _MONTH_MAP[expiry_str[2:5].upper()]
    year  = int(expiry_str[5:])
    return date(year, month, day)


@lru_cache(maxsize=1)
def _fetch_scrip_master() -> tuple:
    """Fetch ScripMaster JSON once per process and cache it in memory.
    The file is ~50 MB so we use a generous timeout and one retry.
    """
    print("[SectorProxy] Fetching ScripMaster (~50 MB) to resolve sector tokens...")
    for attempt in range(1, 3):  # 2 attempts
        try:
            resp = requests.get(_SCRIP_MASTER_URL, timeout=90)
            resp.raise_for_status()
            return tuple(resp.json())
        except requests.exceptions.RequestException as exc:
            if attempt == 2:
                raise RuntimeError(
                    f"[SectorProxy] ScripMaster fetch failed after 2 attempts: {exc}\n"
                    "  Check network connectivity or try again in a moment."
                ) from exc
            print(f"[SectorProxy] Attempt {attempt} timed out — retrying...")


def get_active_futures_token(underlying_name: str) -> dict:
    """
    Returns the nearest-expiry FUTIDX contract for ANY underlying index.
    Auto-rolls monthly. Generalized version of the original BANKNIFTY-only
    resolver — same fix now works for NIFTY, CNXIT/NIFTYIT, etc.

    Parameters
    ----------
    underlying_name : the ScripMaster "name" field to match EXACTLY, e.g.
        "BANKNIFTY", "NIFTY". Uses exact equality, NOT substring match —
        substring matching on "NIFTY" would incorrectly also match
        "BANKNIFTY" and "NIFTYIT" contracts, since both contain "NIFTY".

    Returns dict with keys:
        token    (str)  e.g. '61088'
        symbol   (str)  e.g. 'BANKNIFTY28JUL26FUT'
        expiry   (date) e.g. date(2026, 7, 28)
        exch_seg (str)  always 'NFO'

    Raises RuntimeError if no active contract is found — this is a LOUD,
    immediate failure (unlike the original bug it replaces, which silently
    returned 0 candles). If this fires, the underlying_name likely doesn't
    match ScripMaster's exact "name" field — inspect a sample entry to check.
    """
    today = date.today()
    candidates = []

    for item in _fetch_scrip_master():
        if (item.get("exch_seg") == "NFO"
                and item.get("name") == underlying_name   # EXACT match — see docstring
                and item.get("instrumenttype") == "FUTIDX"
                and item.get("expiry", "")):
            try:
                expiry_dt = _parse_angel_expiry(item["expiry"])
                if expiry_dt >= today:  # include expiry day (tradeable intraday)
                    candidates.append({
                        "token"   : item["token"],
                        "symbol"  : item["symbol"],
                        "expiry"  : expiry_dt,
                        "exch_seg": "NFO",
                    })
            except (KeyError, ValueError):
                continue

    if not candidates:
        raise RuntimeError(
            f"No active {underlying_name} FUTIDX found in ScripMaster. "
            f"The 'name' field may not match exactly — try inspecting a raw "
            f"ScripMaster entry for this underlying to confirm the correct name."
        )

    candidates.sort(key=lambda x: x["expiry"])
    active = candidates[0]
    print(
        f"[SectorProxy] Using {active['symbol']}  "
        f"token={active['token']}  expiry={active['expiry']}"
    )
    return active


# ---------------------------------------------------------------------------
# ANGEL ONE AUTHENTICATOR
# ---------------------------------------------------------------------------
class AngelAuthenticator:
    """Handles login and token refresh for Angel One SmartAPI.
    Tokens expire daily -- the authenticator re-logs in automatically.
    """

    BASE_URL = "https://apiconnect.angelone.in"

    def __init__(self, api_key: str, client_id: str,
                 mpin: str, totp_secret: str):
        self.api_key     = api_key
        self.client_id   = client_id
        self.mpin        = mpin
        self.totp_secret = totp_secret
        self._auth_token: Optional[str] = None
        self._token_time: Optional[datetime] = None

    def _base_headers(self) -> dict:
        return {
            "Content-Type":      "application/json",
            "Accept":            "application/json",
            "X-UserType":        "USER",
            "X-SourceID":        "WEB",
            "X-ClientLocalIP":   "127.0.0.1",
            "X-ClientPublicIP":  "127.0.0.1",
            "X-MACAddress":      "00:00:00:00:00:00",
            "X-PrivateKey":      self.api_key,
        }

    def _token_is_fresh(self) -> bool:
        """Token valid for 24 hours, refresh after 23 to be safe."""
        if self._auth_token is None or self._token_time is None:
            return False
        return (datetime.now() - self._token_time).seconds < 82800  # 23 hrs

    def get_auth_headers(self) -> dict:
        """Returns headers with a valid auth token. Logs in if needed."""
        if not self._token_is_fresh():
            self._login()
        headers = self._base_headers()
        headers["Authorization"] = f"Bearer {self._auth_token}"
        return headers

    def _login(self) -> None:
        # Base32 secrets must be a multiple of 8 chars long.
        # Angel One (and many apps) omit trailing '=' padding -- add it back.
        secret = self.totp_secret.upper().strip()
        secret += "=" * ((8 - len(secret) % 8) % 8)  # 0 padding if already aligned
        totp = pyotp.TOTP(secret).now()
        payload = {
            "clientcode": self.client_id,
            "password":   self.mpin,
            "totp":       totp,
        }
        resp = requests.post(
            f"{self.BASE_URL}/rest/auth/angelbroking/user/v1/loginByPassword",
            headers=self._base_headers(),
            json=payload,
            timeout=15,
        )
        data = resp.json()
        if not data.get("status"):
            raise ConnectionError(
                f"Angel One login failed: {data.get('message')}\n"
                f"Check your credentials in the config."
            )
        self._auth_token = data["data"]["jwtToken"]
        self._token_time = datetime.now()
        print(f"[Auth] Login successful at {self._token_time.strftime('%H:%M:%S')}")


# ---------------------------------------------------------------------------
# HISTORICAL DATA FETCHER
# ---------------------------------------------------------------------------
class HistoricalDataFetcher:
    """Fetches 5-minute OHLCV data from Angel One SmartAPI.

    Angel One limits: max 30 days per API call for 5-min data.
    For longer periods, automatically splits into 30-day chunks.

    Rate limiting: 1 second between API calls to avoid 429 errors.
    """

    HIST_URL = ("https://apiconnect.angelone.in/rest/secure/angelbroking"
                "/historical/v1/getCandleData")

    def __init__(self, auth: AngelAuthenticator):
        self.auth = auth

    def fetch_candles(self, token: str, exchange: str,
                      from_dt: datetime, to_dt: datetime,
                      interval: str = "FIVE_MINUTE") -> list:
        """Fetch raw candles for one time window. Returns list of
        [timestamp, open, high, low, close, volume] lists."""
        payload = {
            "exchange":    exchange,
            "symboltoken": token,
            "interval":    interval,
            "fromdate":    from_dt.strftime("%Y-%m-%d %H:%M"),
            "todate":      to_dt.strftime("%Y-%m-%d %H:%M"),
        }
        headers = self.auth.get_auth_headers()
        resp = requests.post(self.HIST_URL, headers=headers,
                             json=payload, timeout=20)

        # Diagnostic: reveal WHY a non-JSON response happened, instead of
        # crashing blind on resp.json(). Empty body / non-200 status is
        # commonly a silent IP block or rate limit from the broker's side,
        # not a code bug -- this makes that visible instead of guessing.
        if resp.status_code != 200:
            print(f"  [Fetch] HTTP {resp.status_code} — {resp.reason}")
            print(f"  [Fetch] Response body (first 500 chars): {resp.text[:500]!r}")
            print(f"  [Fetch] Response headers: {dict(resp.headers)}")
            return []

        try:
            data = resp.json()
        except ValueError:
            print(f"  [Fetch] Non-JSON response despite HTTP 200. "
                  f"Body (first 500 chars): {resp.text[:500]!r}")
            return []

        if not data.get("status"):
            print(f"  [Fetch] Warning: {data.get('message')} "
                  f"(token={token}, {from_dt.date()} to {to_dt.date()})")
            return []
        return data.get("data", [])

    def fetch_full_history(self, config: InstrumentConfig,
                           days: int = 365) -> pd.DataFrame:
        """Fetch `days` of 5-min data, splitting into 30-day chunks.
        Returns a DataFrame with DatetimeIndex (bar-close, IST).
        """
        print(f"\n[Fetch] {config.name} ({config.ticker})"
              f" — requesting {days} days of 5-min data")

        to_dt   = datetime.now()
        from_dt = to_dt - timedelta(days=days)

        all_candles = []
        chunk_start = from_dt

        while chunk_start < to_dt:
            chunk_end = min(chunk_start + timedelta(days=29), to_dt)
            print(f"  Chunk: {chunk_start.date()} → {chunk_end.date()}")

            candles = self.fetch_candles(
                config.symbol_token, config.exchange,
                chunk_start, chunk_end
            )
            all_candles.extend(candles)
            chunk_start = chunk_end + timedelta(minutes=5)
            time.sleep(1)  # rate limiting

        df = self._to_dataframe(all_candles)
        print(f"  Raw candles: {len(df)}")
        return df

    def _to_dataframe(self, candles: list) -> pd.DataFrame:
        if not candles:
            return pd.DataFrame()
        df = pd.DataFrame(candles,
                          columns=["datetime", "open", "high",
                                   "low", "close", "volume"])
        # Parse timestamps — Angel One returns ISO8601 with IST offset
        df["datetime"] = pd.to_datetime(df["datetime"], utc=False)

        # Normalize to bar-CLOSE timestamp (architecture W19 requirement)
        # Angel One returns bar-START timestamps — add 5 minutes
        df["datetime"] = df["datetime"] + pd.Timedelta(minutes=5)

        df = df.set_index("datetime")
        df = df.astype({"open": float, "high": float,
                        "low": float, "close": float, "volume": float})
        df = df.sort_index()
        df = df[~df.index.duplicated(keep="last")]  # remove duplicates
        return df


# ---------------------------------------------------------------------------
# MISSING BAR DETECTOR (W22)
# ---------------------------------------------------------------------------
class MissingBarDetector:
    """Detects sessions with missing 5-min bars.
    NSE session: 9:15 AM to 3:30 PM = 75 bars per session.
    Missing bars silently corrupt VWAP, ATR, and bar counters.
    """

    EXPECTED_BARS_PER_SESSION = 75  # 9:15 to 3:30 in 5-min steps
    SESSION_OPEN  = (9, 20)   # bar-close: 9:15 bar closes at 9:20
    SESSION_CLOSE = (15, 30)  # bar-close: 3:25 bar closes at 3:30

    def check(self, df: pd.DataFrame) -> pd.DataFrame:
        """Tags each row with bar_gap flag.
        Also prints a summary of affected sessions.
        Returns df with added 'bar_gap' column.
        """
        df = df.copy()
        df["bar_gap"] = False
        df["date"]    = df.index.date

        gap_sessions = []
        for date, group in df.groupby("date"):
            expected = self.EXPECTED_BARS_PER_SESSION
            actual   = len(group)
            if actual < expected * 0.90:  # >10% missing bars
                gap_sessions.append({
                    "date":     date,
                    "expected": expected,
                    "actual":   actual,
                    "missing":  expected - actual,
                })
                df.loc[df["date"] == date, "bar_gap"] = True

        if gap_sessions:
            print(f"\n[MissingBars] {len(gap_sessions)} sessions with"
                  f" >10% missing bars:")
            for s in gap_sessions[:5]:
                print(f"  {s['date']}: {s['actual']}/{s['expected']} bars"
                      f" ({s['missing']} missing)")
            if len(gap_sessions) > 5:
                print(f"  ... and {len(gap_sessions) - 5} more")
        else:
            print("[MissingBars] No significant gaps detected ✓")

        df = df.drop(columns=["date"])
        return df


# ---------------------------------------------------------------------------
# RVOL_ToD CALCULATOR (W18 — no leakage)
# ---------------------------------------------------------------------------
class RVOLCalculator:
    """Computes time-of-day relative volume (RVOL_ToD) using an
    EXPANDING window — at any time T, only sessions PRIOR to T
    contribute to the denominator.

    This is the mandatory leakage guard described in W18 of the
    architecture. The naive implementation (computing means across
    the full dataset then applying them backwards) uses future data
    and silently inflates backtest win rates.
    """

    def compute(self, df: pd.DataFrame,
                lookback_sessions: int = 20) -> pd.DataFrame:
        """Adds RVOL_ToD column. Uses up to lookback_sessions prior
        sessions for the time-slot average. For the first few sessions
        where fewer than 3 prior sessions exist, RVOL_ToD is NaN
        (not enough history to compute reliably)."""
        df = df.copy()
        df["time_slot"]  = df.index.strftime("%H:%M")
        df["date"]       = df.index.date
        df["rvol_tod"]   = np.nan

        # Get ordered list of unique sessions
        sessions = sorted(df["date"].unique())

        for i, current_date in enumerate(sessions):
            if i < 3:
                # Not enough history yet — leave as NaN
                continue

            # Get prior sessions (expanding window, capped at lookback)
            prior_start = max(0, i - lookback_sessions)
            prior_sessions = sessions[prior_start:i]

            prior_data = df[df["date"].isin(prior_sessions)]

            # Compute mean volume per time slot from prior sessions only
            slot_means = (prior_data.groupby("time_slot")["volume"]
                                    .mean()
                                    .to_dict())

            # Apply to current session
            current_mask = df["date"] == current_date
            current_slots = df.loc[current_mask, "time_slot"]
            slot_avgs = current_slots.map(slot_means)

            # RVOL = today's volume / prior-sessions average for same slot
            df.loc[current_mask, "rvol_tod"] = (
                df.loc[current_mask, "volume"] / slot_avgs
            )

        df = df.drop(columns=["time_slot", "date"])
        return df


# ---------------------------------------------------------------------------
# DATA VALIDATOR
# ---------------------------------------------------------------------------
class DataValidator:
    """Final validation pass before data is handed to downstream
    components. Checks for obviously corrupt values."""

    def validate(self, df: pd.DataFrame, name: str) -> pd.DataFrame:
        issues = []

        # Check for NaN in OHLCV
        ohlcv = ["open", "high", "low", "close", "volume"]
        nan_counts = df[ohlcv].isna().sum()
        if nan_counts.any():
            issues.append(f"NaN values: {nan_counts[nan_counts > 0].to_dict()}")

        # Check high >= low
        invalid_hl = (df["high"] < df["low"]).sum()
        if invalid_hl:
            issues.append(f"{invalid_hl} bars where high < low")

        # Check volume > 0
        zero_vol = (df["volume"] <= 0).sum()
        if zero_vol:
            issues.append(f"{zero_vol} bars with zero/negative volume")

        # Check timestamps are within NSE session hours (IST)
        # bar-close timestamps: 9:20 to 15:30
        hours = df.index.hour
        outside_session = ((hours < 9) | (hours > 15)).sum()
        if outside_session:
            issues.append(f"{outside_session} bars outside session hours")

        if issues:
            print(f"\n[Validate] {name} — {len(issues)} issue(s) found:")
            for issue in issues:
                print(f"  ⚠ {issue}")
        else:
            print(f"[Validate] {name} — data looks clean ✓")

        return df


# ---------------------------------------------------------------------------
# LOCAL CACHE
# ---------------------------------------------------------------------------
class LocalCache:
    """Saves fetched data to disk so repeat runs don't hit the API.
    Cache is invalidated if data is older than 1 day (stale).
    """

    def __init__(self, cache_dir: str = "data_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)

    def _path(self, key: str) -> Path:
        safe_key = key.replace("/", "_").replace(" ", "_")
        return self.cache_dir / f"{safe_key}.parquet"

    def exists(self, key: str, max_age_hours: int = 20) -> bool:
        p = self._path(key)
        if not p.exists():
            return False
        age = datetime.now() - datetime.fromtimestamp(p.stat().st_mtime)
        age_h = age.total_seconds() / 3600
        fresh = age_h < max_age_hours
        status = f"fresh ({age_h:.1f}h old)" if fresh else f"STALE ({age_h:.1f}h old) — will re-fetch"
        print(f"[Cache] {key}: {status}")
        return fresh  # .seconds only gives sub-day component — must use total_seconds()!

    def save(self, key: str, df: pd.DataFrame) -> None:
        df.to_parquet(self._path(key))
        print(f"[Cache] Saved {key} ({len(df)} rows)")

    def load(self, key: str) -> pd.DataFrame:
        df = pd.read_parquet(self._path(key))
        print(f"[Cache] Loaded {key} from cache ({len(df)} rows)")
        return df


# ---------------------------------------------------------------------------
# MAIN PIPELINE
# ---------------------------------------------------------------------------
class AngelDataPipeline:
    """Orchestrates the full data pipeline:
    login → fetch → normalize → missing-bar check → RVOL_ToD → validate → cache.

    This is the single entry point for all downstream components.
    Every component in the system receives data from this pipeline,
    never from the API directly.
    """

    def __init__(self, api_key: str, client_id: str,
                 mpin: str, totp_secret: str,
                 cache_dir: str = "data_cache"):
        self.auth      = AngelAuthenticator(api_key, client_id,
                                            mpin, totp_secret)
        self.fetcher   = HistoricalDataFetcher(self.auth)
        self.gap_check = MissingBarDetector()
        self.rvol_calc = RVOLCalculator()
        self.validator = DataValidator()
        self.cache     = LocalCache(cache_dir)

    # RVOL_ToD needs prior sessions WITHIN the same fetch to build its
    # expanding-window baseline (see RVOLCalculator) — it has no memory of
    # earlier script runs. Without a buffer, whichever ~3 calendar days land
    # at the very start of a `days`-sized fetch get rvol_tod=NaN (i<3 in
    # RVOLCalculator), and the next several past that have only a thin,
    # noisy few-day baseline. This makes Phase A/B detection for early-window
    # sessions silently depend on exactly where the 30-day window happened to
    # start on a given run -- the same calendar day can gain or lose a signal
    # purely based on run timing, with no code change and no market change.
    #
    # Fix: fetch `days + WARMUP_CALENDAR_DAYS` extra calendar days, compute
    # RVOL over that full extended range (so the requested `days` window is
    # fully warmed up), then trim back down to exactly `days` before caching
    # or returning. ~30 calendar days covers RVOLCalculator's
    # lookback_sessions=20 trading-day cap even after weekends/holidays.
    WARMUP_CALENDAR_DAYS = 30

    def _fetch_compute_trim(
        self, config: InstrumentConfig, days: int, validate_label: str
    ) -> pd.DataFrame:
        """Shared fetch -> gap-check -> RVOL -> trim -> validate pipeline.
        Used for BOTH stock and sector fetches so both get a properly
        warmed-up RVOL_ToD baseline and identical treatment — previously
        this logic was duplicated (and had drifted slightly) between
        fetch() and fetch_with_sector()."""
        fetch_days = days + self.WARMUP_CALENDAR_DAYS
        df = self.fetcher.fetch_full_history(config, days=fetch_days)
        if df.empty:
            raise ValueError(f"No data returned for {config.ticker}")

        df = self.gap_check.check(df)  # Missing bar detection (W22)

        print("[RVOL] Computing time-of-day relative volume...")
        df = self.rvol_calc.compute(df)  # expanding window over the FULL fetch — no leakage (W18)

        # Trim the warm-up buffer back off — it existed only to give RVOL_ToD
        # a real baseline, it isn't part of what the caller actually asked for.
        cutoff = df.index.max() - pd.Timedelta(days=days)
        df = df[df.index > cutoff]

        self.validator.validate(df, validate_label)
        return df

    def fetch(self, config: InstrumentConfig,
              days: int = 365,
              use_cache: bool = True) -> pd.DataFrame:
        """Full pipeline for one instrument. Returns clean DataFrame
        ready for SessionFeatureEngineer, with a properly warmed-up
        RVOL_ToD baseline for every session in the returned window
        (see WARMUP_CALENDAR_DAYS above).

        Columns:
          open, high, low, close, volume   — OHLCV (float)
          rvol_tod                          — time-of-day rel. volume
          bar_gap                           — True if session had missing bars
        Index:
          DatetimeIndex, bar-CLOSE timestamps, IST timezone
        """
        cache_key = f"{config.ticker}_{days}d_5min"

        if use_cache and self.cache.exists(cache_key):
            return self.cache.load(cache_key)

        df = self._fetch_compute_trim(config, days, config.ticker)
        self.cache.save(cache_key, df)
        return df

    def fetch_with_sector(self, config: InstrumentConfig,
                          days: int = 365,
                          use_cache: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Fetch both the stock and its sector index.
        Returns (stock_df, sector_df).

        Both DataFrames use the same bar-close timestamp convention (W19).
        The caller must align them by timestamp before computing
        sector-relative features.
        """
        stock_df = self.fetch(config, days=days, use_cache=use_cache)

        sector_key = f"{config.sector_index}_{days}d_5min"

        # ── Cache check FIRST — avoids ScripMaster fetch when sector data is fresh ──
        if use_cache and self.cache.exists(sector_key):
            sector_df = self.cache.load(sector_key)
            self._check_date_alignment(stock_df, sector_df, config)
            return stock_df, sector_df

        # ── Cache miss — resolve token then fetch fresh sector data ──
        resolved_token = config.sector_token
        resolved_exch  = config.sector_exchange

        if not resolved_token:
            # Empty sector_token means "auto-resolve via nearest FUTIDX contract"
            # (same fix as originally built for BANKNIFTY, now generalized —
            # ALL NSE index tokens return 0 candles from Angel One's history
            # API, not just BANKNIFTY's).
            fut = get_active_futures_token(config.sector_index)
            resolved_token = fut["token"]
            resolved_exch  = fut["exch_seg"]  # "NFO"

        sector_config = InstrumentConfig(
            ticker        = config.sector_index,
            symbol_token  = resolved_token,
            exchange      = resolved_exch,
            sector_index  = "",
            sector_token  = "",
            sector_exchange = "NSE",
            gap_threshold = config.gap_threshold,
            name          = f"{config.sector_index} Proxy"
        )
        sector_df = self._fetch_compute_trim(sector_config, days, config.sector_index)
        self.cache.save(sector_key, sector_df)

        self._check_date_alignment(stock_df, sector_df, config)
        return stock_df, sector_df

    @staticmethod
    def _check_date_alignment(
        stock_df: pd.DataFrame, sector_df: pd.DataFrame, config: "InstrumentConfig"
    ) -> None:
        """
        Guards against a specific silent-drift bug: stock and sector data are
        cached with INDEPENDENT 20h TTLs, so it's possible for one to be
        re-fetched while the other still serves an older cached window. Both
        might report similar row counts and look fine individually, while the
        actual DATE RANGES no longer overlap correctly — corrupting sector-
        relative features (I2, S3) for the misaligned days without any error.

        This doesn't force a re-fetch (that would fight the caching system's
        purpose) — it just makes the drift LOUD instead of silent, so a run
        with misaligned caches is visibly flagged rather than quietly
        producing subtly wrong signals.
        """
        stock_start,  stock_end  = stock_df.index.min(),  stock_df.index.max()
        sector_start, sector_end = sector_df.index.min(), sector_df.index.max()

        gap_start = abs((stock_start - sector_start).total_seconds()) / 86400
        gap_end   = abs((stock_end   - sector_end).total_seconds())   / 86400

        if gap_start > 2 or gap_end > 2:  # more than 2 calendar days apart
            print(
                f"[DateAlign] ⚠ {config.ticker} vs {config.sector_index} date ranges "
                f"differ by {max(gap_start, gap_end):.1f} days "
                f"(stock: {stock_start.date()}→{stock_end.date()}, "
                f"sector: {sector_start.date()}→{sector_end.date()}). "
                f"Caches likely drifted out of sync — consider deleting both "
                f"parquet files and re-running for a clean joint fetch."
            )


# ---------------------------------------------------------------------------
# QUICK TEST
# ---------------------------------------------------------------------------