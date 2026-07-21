"""
session_feature_engineer.py — Step 2: Session Feature Engineering
==================================================================
Takes aligned stock + sector DataFrames from Step 1 (AngelDataPipeline)
and produces a single feature-enriched DataFrame for the signal model (Step 3).

All features are computed in a strict lookahead-free manner:
  - OR features are NaN during the OR period itself (computed only after bar 5 closes)
  - VWAP is cumulative within the session — only uses past bars
  - ATR is a rolling window that crosses sessions (no reset)
  - Gap is computed from previous session's last close vs current session's first open

Output columns (on top of original OHLCV + rvol_tod + bar_gap):
  Session metadata  : session_date, bar_index, phase
  Opening Range     : or_high, or_low, or_range, or_mid
  Price position    : close_pos, above_or_high, dist_from_or_high
  VWAP (stock)      : vwap, above_vwap, dist_from_vwap
  Sector            : sector_close, sector_vwap, sector_above_vwap
  ATR               : atr
  Gap               : gap_pct, gap_regime
  Prev session      : prev_day_high

Usage:
    from session_feature_engineer import SessionFeatureEngineer
    from angel_data_pipeline import AngelDataPipeline, InstrumentConfig
    from config import CREDENTIALS, SETTINGS

    pipeline = AngelDataPipeline(**CREDENTIALS, cache_dir=SETTINGS.cache_dir)
    config   = InstrumentConfig.HDFCBANK()
    stock_df, sector_df = pipeline.fetch_with_sector(config, days=30)

    sfe         = SessionFeatureEngineer(or_end_bar=SETTINGS.or_end_bar)
    features_df = sfe.compute(stock_df, sector_df, config)
"""

from __future__ import annotations

from datetime import time

import numpy as np
import pandas as pd


class SessionFeatureEngineer:
    """
    Enriches raw OHLCV data with all features required by TwoPhaseSignalModel.

    Parameters
    ----------
    or_end_bar : int
        Number of 5-min bars that define the Opening Range (default 6 = 30 min).
        Bars 0..or_end_bar-1 are labelled phase="OR".
    ATR_PERIOD : 14 (class constant)
        Wilder's 14-period daily ATR — resampled from 5-min bars, not a rolling 5-min window.
    """

    # Phase boundary times (IST) — align with architecture v1.4
    _ENTRY_CUTOFF   = time(14, 30)   # no new entries at or after this bar
    _SQUAREOFF_TIME = time(15, 15)   # hard square-off, phase = CLOSE

    ATR_PERIOD = 14   # Wilder's standard; daily bars eliminate 5-min microstructure noise

    def __init__(self, or_end_bar: int = 6) -> None:
        self.or_end_bar = or_end_bar

    # ── Public API ─────────────────────────────────────────────────────────────

    def compute(
        self,
        stock_df: pd.DataFrame,
        sector_df: pd.DataFrame,
        config,                        # InstrumentConfig from angel_data_pipeline
    ) -> pd.DataFrame:
        """
        Compute all session features.

        Parameters
        ----------
        stock_df  : DataFrame from AngelDataPipeline.fetch_with_sector()
        sector_df : Aligned sector DataFrame (same pipeline call)
        config    : InstrumentConfig — provides gap_threshold and ticker name

        Returns
        -------
        DataFrame with original columns + all feature columns, indexed by datetime.
        """
        df = stock_df.copy()

        # Order matters: session_date / bar_index must exist before later steps
        df = self._add_session_metadata(df)    # 1
        df = self._add_atr(df)                 # 2  (cross-session — before any groupby)
        df = self._add_opening_range(df)       # 3
        df = self._add_intraday_vwap(df)       # 4
        df = self._add_gap_features(df, config.gap_threshold)  # 5
        df = self._add_prev_day_high(df)       # 6
        df = self._add_price_features(df)      # 7  (needs OR + VWAP from steps 3-4)
        df = self._add_sector_features(df, sector_df)          # 8
        self._validate(df, config.ticker)      # 9

        return df

    # ── Step 1: Session metadata ───────────────────────────────────────────────

    def _add_session_metadata(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Adds session_date (Python date), bar_index (0-based int), and phase (str).

        Phase labels:
          OR      — bar_index 0 .. or_end_bar-1 (Opening Range accumulation)
          ACTIVE  — after OR, before 14:30 (entry window)
          CUTOFF  — 14:30 to 15:14 (no new entries; manage existing positions)
          CLOSE   — 15:15 onwards (hard square-off zone)
        """
        # .normalize() zeroes the time while keeping the IST timezone,
        # then .date strips timezone → plain Python date object.
        df["session_date"] = df.index.normalize().date
        df["bar_index"]    = df.groupby("session_date").cumcount()
        df["phase"]        = self._compute_phases(df)
        return df

    def _compute_phases(self, df: pd.DataFrame) -> pd.Series:
        bar_time = np.array(df.index.time)          # array of datetime.time objects
        in_or    = (df["bar_index"].to_numpy() < self.or_end_bar)
        before_cutoff    = np.array([t < self._ENTRY_CUTOFF   for t in bar_time])
        before_squareoff = np.array([t < self._SQUAREOFF_TIME for t in bar_time])

        phase = np.where(
            in_or, "OR",
            np.where(before_cutoff, "ACTIVE",
            np.where(before_squareoff, "CUTOFF", "CLOSE"))
        )
        return pd.Series(phase, index=df.index, dtype="object")

    # ── Step 2: ATR (daily, Wilder's method) ─────────────────────────────────────

    def _add_atr(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        14-period Wilder's ATR computed on DAILY bars resampled from 5-min data.

        Why daily (not 5-min rolling):
          - 5-min rolling ATR is noisy at session open — exactly when Phase A fires
          - Daily ATR represents "typical full-day range" — meaningful stop sizing
          - Stop at ATR×1.5 means "wrong if price moves 1.5 typical days against me"
          - Stable within session: position size won't change bar-to-bar

        Each session gets the PREVIOUS day's ATR (shift=1) — strictly lookahead-free.
        NaN for the first session in the dataset (no prior day available).
        """
        # Step 1: Resample 5-min → daily OHLC (dropna removes weekends/holidays)
        daily = (
            df[["open", "high", "low", "close"]]
            .resample("1D")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna(subset=["close"])
        )

        # Step 2: True Range per daily bar
        prev_close = daily["close"].shift(1)
        tr = pd.concat([
            daily["high"] - daily["low"],
            (daily["high"] - prev_close).abs(),
            (daily["low"]  - prev_close).abs(),
        ], axis=1).max(axis=1)

        # Step 3: Wilder's ATR = EWM with alpha = 1/period
        #   Equivalent to: ATR_n = ATR_{n-1} × (13/14) + TR_n × (1/14)
        daily["atr"] = tr.ewm(alpha=1 / self.ATR_PERIOD, adjust=False).mean()

        # Step 4: Each session uses PREVIOUS day's ATR — shift(1) on daily
        daily["atr_prev"] = daily["atr"].shift(1)

        # Step 5: Map daily ATR back to 5-min bars via session_date
        #   daily.index is tz-aware midnight IST; convert to plain date to match
        #   session_date which was computed as .normalize().date
        date_to_atr = {
            ts.date(): atr
            for ts, atr in daily["atr_prev"].items()
        }
        df["atr"] = df["session_date"].map(date_to_atr)
        df["atr"] = df["atr"].bfill()   # session-1 NaN: no prior day — fill with session-2 ATR
        return df

    # ── Step 3: Opening Range ──────────────────────────────────────────────────

    def _add_opening_range(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        or_high / or_low / or_range / or_mid — constant across all bars of the session.

        IMPORTANT: these columns will be NaN for the first session in the dataset
        if OR bars don't exist (edge case), and will be correctly available from
        bar_index = or_end_bar onwards. The signal model should only consume OR
        features when phase != 'OR'.
        """
        or_bars = df.loc[df["phase"] == "OR", ["session_date", "high", "low"]]
        or_agg  = or_bars.groupby("session_date").agg(
            or_high=("high", "max"),
            or_low =("low",  "min"),
        )
        or_agg["or_range"] = or_agg["or_high"] - or_agg["or_low"]
        or_agg["or_mid"]   = (or_agg["or_high"] + or_agg["or_low"]) / 2

        # Broadcast session-level values to all bars in that session
        df = df.join(or_agg, on="session_date")
        return df

    # ── Step 4: Intraday VWAP (resets each session) ───────────────────────────

    def _add_intraday_vwap(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Cumulative VWAP within each session.

        Typical Price = (H + L + C) / 3
        VWAP = cumsum(TP × Volume) / cumsum(Volume)

        Cumulative means each bar's VWAP only uses data from the current session's
        start up to and including that bar — strictly lookahead-free.
        """
        tp     = (df["high"] + df["low"] + df["close"]) / 3
        tp_vol = tp * df["volume"]

        def _session_vwap(grp_idx):
            cum_tp_vol = tp_vol.loc[grp_idx].cumsum()
            cum_vol    = df.loc[grp_idx, "volume"].cumsum().replace(0, np.nan)
            return cum_tp_vol / cum_vol

        vwap_series = df.groupby("session_date", group_keys=False).apply(
            lambda g: _session_vwap(g.index)
        )
        df["vwap"] = vwap_series
        return df

    # ── Step 5: Gap features (session-level) ──────────────────────────────────

    def _add_gap_features(self, df: pd.DataFrame, gap_threshold: float) -> pd.DataFrame:
        """
        Gap is computed once per session from previous session's last close
        vs current session's first open.

        gap_pct    : (today_open - prev_close) / prev_close
        gap_regime : 'WIDE' if |gap_pct| > gap_threshold (0.8%), else 'FLAT'

        First session in the dataset → gap_pct = NaN, gap_regime = 'FLAT'.
        """
        # Session-level series indexed by session_date
        prev_close   = df.groupby("session_date")["close"].last().shift(1)
        session_open = df.groupby("session_date")["open"].first()

        gap_pct    = (session_open - prev_close) / prev_close
        gap_regime = gap_pct.abs().map(
            lambda x: "WIDE" if pd.notna(x) and x > gap_threshold else "FLAT"
        )

        gap_df = pd.DataFrame({"gap_pct": gap_pct, "gap_regime": gap_regime})
        df = df.join(gap_df, on="session_date")
        return df

    # ── Step 6: Previous day high (session-level) ──────────────────────────────

    def _add_prev_day_high(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Used for Phase B scoring S6: close above prev_day_high = +1.
        NaN for the first session in the dataset.
        """
        daily_high    = df.groupby("session_date")["high"].max()
        prev_day_high = daily_high.shift(1)
        df = df.join(prev_day_high.rename("prev_day_high"), on="session_date")
        return df

    # ── Step 7: Per-bar price position features ────────────────────────────────

    def _add_price_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        All per-bar features that depend on OR and VWAP (computed in steps 3-4).

        close_pos        : where the close sits within the bar's H-L range (0-1)
                           0.5 for doji candles (H == L)
        above_or_high    : bool — close strictly above or_high
        dist_from_or_high: (close - or_high) / or_high — signed, pct
        above_vwap       : bool — close strictly above intraday VWAP
        dist_from_vwap   : (close - vwap) / vwap — signed, pct
        """
        bar_range       = (df["high"] - df["low"]).replace(0, np.nan)
        df["close_pos"] = ((df["close"] - df["low"]) / bar_range).fillna(0.5)

        df["above_or_high"]     = df["close"] > df["or_high"]
        df["dist_from_or_high"] = (df["close"] - df["or_high"]) / df["or_high"]

        df["above_vwap"]     = df["close"] > df["vwap"]
        df["dist_from_vwap"] = (df["close"] - df["vwap"]) / df["vwap"]
        return df

    # ── Step 8: Sector features ────────────────────────────────────────────────

    def _add_sector_features(
        self, df: pd.DataFrame, sector_df: pd.DataFrame
    ) -> pd.DataFrame:
        """
        Computes sector VWAP (same logic as stock VWAP, resets per session)
        and joins sector_close + sector_vwap + sector_above_vwap to stock bars.

        Used by signal model for:
          I2: invalidation if sector_above_vwap False for 2+ consecutive bars
          S3: Phase B score +2 if sector direction matches trade direction
        """
        sec = sector_df[["high", "low", "close", "volume"]].copy()
        sec.columns = ["s_high", "s_low", "s_close", "s_volume"]
        sec["_session_date"] = sec.index.normalize().date

        # Sector intraday VWAP
        s_tp     = (sec["s_high"] + sec["s_low"] + sec["s_close"]) / 3
        s_tp_vol = s_tp * sec["s_volume"]

        def _sec_session_vwap(grp_idx):
            cum_tp_vol = s_tp_vol.loc[grp_idx].cumsum()
            cum_vol    = sec.loc[grp_idx, "s_volume"].cumsum().replace(0, np.nan)
            return cum_tp_vol / cum_vol

        sec_vwap = sec.groupby("_session_date", group_keys=False).apply(
            lambda g: _sec_session_vwap(g.index)
        )
        sec["sector_vwap"]       = sec_vwap
        sec["sector_above_vwap"] = sec["s_close"] > sec["sector_vwap"]

        # Consecutive bars where sector is BELOW vwap — used for I2 invalidation:
        #   "cancel Phase A setup if sector below VWAP for 2+ consecutive bars
        #    (only applies after 10:15 AM)"
        # Vectorized: groupby session, use cumsum trick to count runs
        def _consec_below(above_series: pd.Series) -> pd.Series:
            below = (~above_series).astype(int)
            # Identify run boundaries (value changes between consecutive bars)
            run_id = (below != below.shift(fill_value=0)).cumsum()
            # Within each run, cumcount gives 0,1,2,... — add 1 and zero out "above" bars
            counts = below.groupby(run_id).cumcount() + 1
            return (counts * below).astype(int)

        sec["sector_consec_below_vwap"] = (
            sec.groupby("_session_date", group_keys=False)["sector_above_vwap"]
            .transform(_consec_below)
        )

        # Join using left join — stock bars without a matching sector bar get NaN
        df = df.join(
            sec[["s_close", "sector_vwap", "sector_above_vwap", "sector_consec_below_vwap"]].rename(
                columns={"s_close": "sector_close"}
            ),
            how="left",
        )
        return df

    # ── Step 9: Validation ─────────────────────────────────────────────────────

    def _validate(self, df: pd.DataFrame, ticker: str) -> None:
        """
        Sanity-check key features on ACTIVE/CUTOFF bars.
        Prints a pass/fail report analogous to the W18/W19 checks in Step 1.
        """
        print(f"\n{'='*55}")
        print(f"[SFE Validation] {ticker}")
        print(f"{'='*55}")

        active = df[df["phase"].isin(["ACTIVE", "CUTOFF", "CLOSE"])]
        issues = []

        # ── NaN checks on critical columns ──
        critical_cols = {
            "or_high"                   : "Opening range high",
            "vwap"                      : "Stock VWAP",
            "atr"                       : "ATR",
            "sector_vwap"               : "Sector VWAP",
            "sector_above_vwap"         : "Sector above VWAP flag",
            "sector_consec_below_vwap"  : "Sector consec below VWAP (I2)",
        }
        for col, label in critical_cols.items():
            nan_pct = active[col].isna().mean()
            if nan_pct > 0.02:
                issues.append(f"{label} ({col}): {nan_pct:.1%} NaN")
            else:
                print(f"  {label:<30} ✓  (NaN={nan_pct:.1%})")

        # ── OR range must be positive ──
        or_zero = (df.groupby("session_date")["or_range"].first() == 0).sum()
        if or_zero:
            issues.append(f"or_range = 0 on {or_zero} session(s)")
        else:
            print(f"  {'OR range positive':<30} ✓")

        # ── ATR must be positive everywhere ──
        bad_atr = (df["atr"] <= 0).sum()
        if bad_atr:
            issues.append(f"ATR <= 0 in {bad_atr} bars")
        else:
            print(f"  {'ATR > 0 everywhere':<30} ✓")

        # ── close_pos must be in [0, 1] ──
        bad_cp = ((df["close_pos"] < 0) | (df["close_pos"] > 1)).sum()
        if bad_cp:
            issues.append(f"close_pos out of [0,1] in {bad_cp} bars")
        else:
            print(f"  {'close_pos in [0,1]':<30} ✓")

        # ── Summary ──
        sessions = df["session_date"].nunique()
        or_range_avg  = df.groupby("session_date")["or_range"].first().mean()
        atr_avg       = df["atr"].mean()
        gap_counts    = df.groupby("session_date")["gap_regime"].first().value_counts().to_dict()
        phase_counts  = df["phase"].value_counts().to_dict()

        print(f"\n  Bars total    : {len(df):,}  across {sessions} sessions")
        print(f"  Phase counts  : {phase_counts}")
        print(f"  OR range avg  : {or_range_avg:.2f}")
        print(f"  ATR avg       : {atr_avg:.2f}")
        print(f"  Gap regimes   : {gap_counts}")

        if issues:
            print(f"\n  ⚠ {len(issues)} issue(s) found:")
            for iss in issues:
                print(f"    - {iss}")
        else:
            print(f"\n  All checks passed ✓")
            print(f"  Next step: TwoPhaseSignalModel")


# ── CLI test ───────────────────────────────────────────────────────────────────