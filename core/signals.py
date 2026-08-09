"""
two_phase_signal_model.py — Step 3: Two-Phase Signal Model
============================================================
Consumes the enriched DataFrame from SessionFeatureEngineer (Step 2)
and generates entry signals following the v1.4 architecture exactly.

PHASE A — Setup bar detection:
    Triggers when ALL of:
      - phase == "ACTIVE" (after OR, before 14:30)
      - close > or_high
      - rvol_tod > 1.5
      - close_pos > 0.65
      - No invalidation condition active (I1–I4)

    Invalidation conditions (any one cancels the setup):
      I1a: close < or_high × (1 - 0.001)     — closed back below OR high by 0.1%
      I1b: pullback_depth > 1.0              — price round-tripped through OR_H and
                                                beyond, checked on the SAME scale as
                                                pullback_depth (breakout-move-relative,
                                                not a price %) — a price-% tolerance
                                                silently never fires when the breakout
                                                move itself is small
      I2:  sector_consec_below_vwap >= 2     — sector weak for 2+ bars (only after 10:15)
      I3:  or_low broken (close < or_low)    — opposite boundary violated
      I4:  price ran > 1.5× ATR above or_high before pullback
      I5:  daily loss limit hit              — checked externally by RiskManager (Step 4)

PHASE B — Entry bar scoring (within 5 bars after Phase A):
    Score each pullback bar. Enter if score >= 3.

    Scoring (max 8 points):
      S1: close > vwap           = +2  (only after 10:15 AM)
      S2: within 1% of vwap      = +1  (ONLY if S1 passed)
      S3: sector_above_vwap      = +2
      S4: pullback_depth < 50%   = +1  (how deep pullback goes below Phase A close)
      S5: positive bar body      = +1  (close >= open)
      S6: close > prev_day_high  = +1

Output per bar (added columns):
    phase_a_active  bool   True on the bar that IS the Phase A setup bar
    phase_b_window  bool   True during the 5-bar wait after Phase A
    phase_b_score   int    Score computed on each Phase B candidate bar (0 if not in window)
    signal          str    "LONG" on the first Phase B bar that hits score >= 3, else ""
    signal_price    float  Close price of the signal bar (entry reference)
    signal_bar_idx  int    bar_index of the signal bar within the session
    phase_a_bar_idx int    bar_index of the Phase A setup bar (broadcast to session)
    phase_a_close   float  Close of Phase A bar (used for pullback_depth calc)
    pullback_depth  float  (phase_a_close - bar_low) / (phase_a_close - or_high)
                           0 if no active Phase A
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


# ── Constants ─────────────────────────────────────────────────────────────────
_10_15 = datetime.time(10, 15)   # I2 only applies after this time
_14_30 = datetime.time(14, 30)   # entry cutoff


@dataclass
class SignalConfig:
    """All tunable thresholds in one place — matches SETTINGS in config.py."""
    rvol_threshold:      float = 1.5    # Phase A: min RVOL
    close_pos_min:       float = 0.65   # Phase A: min close position
    breakout_buffer_pct: float = 0.0015 # A2: close must clear OR_H by this % (v1.4 spec)
    max_pullback_bars:   int   = 5      # Phase B: cancel setup after N bars
    score_threshold:     int   = 3      # Phase B: min score to enter
    i1_pct:              float = 0.001  # I1: close drops 0.1% below or_high
    i4_atr_mult:         float = 1.5    # I4: price ran > 1.5×ATR above or_high
    s2_vwap_pct:         float = 0.01   # S2: within 1% of VWAP
    s4_pullback_max:     float = 0.50   # S4: pullback < 50% of (highest_high - or_high)

    @classmethod
    def from_settings(cls, settings) -> "SignalConfig":
        return cls(
            rvol_threshold      = settings.rvol_threshold,
            close_pos_min       = settings.close_pos_min,
            breakout_buffer_pct = settings.breakout_buffer_pct,
            max_pullback_bars   = settings.max_pullback_bars,
            score_threshold     = settings.score_threshold,
        )


@dataclass
class _SessionState:
    """Mutable state for one trading session — reset at each session boundary."""
    phase_a_bar_idx:  Optional[int]   = None
    phase_a_close:    Optional[float] = None
    highest_high:     Optional[float] = None   # v1.4 "highest_high_since_break" — init at Phase A close, updated each wait bar
    bars_since_a:     int             = 0
    signal_fired:     bool            = False   # only one signal per session
    phase_a_seen:     bool            = False   # one Phase A attempt per session max
    awaiting_fill:    bool            = False   # Phase B scored on prior bar -- fill at THIS bar's open (v1.4: no look-ahead)
    pending_score:    int             = 0       # carried from trigger bar to fill bar, for output clarity
    pending_depth:    Optional[float] = None    # carried from trigger bar to fill bar, for output clarity

    @property
    def waiting_for_b(self) -> bool:
        return self.phase_a_bar_idx is not None and not self.signal_fired


class TwoPhaseSignalModel:
    """
    Detects Phase A setup bars and Phase B entry signals on a feature DataFrame.

    Usage:
        model      = TwoPhaseSignalModel(SignalConfig.from_settings(SETTINGS))
        signals_df = model.generate(features_df)
    """

    def __init__(self, config: Optional[SignalConfig] = None) -> None:
        self.cfg = config or SignalConfig()

    # ── Public API ─────────────────────────────────────────────────────────────

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Main entry point.

        Parameters
        ----------
        df : enriched DataFrame from SessionFeatureEngineer.compute()

        Returns
        -------
        df with added signal columns (listed in module docstring).
        """
        self._validate_input(df)

        # Pre-allocate output columns
        out = df.copy()
        out["phase_a_active"]  = False
        out["phase_b_window"]  = False
        out["phase_b_score"]   = 0
        out["signal"]          = ""
        out["signal_price"]    = np.nan
        out["signal_bar_idx"]  = -1
        out["phase_a_bar_idx"] = -1
        out["phase_a_close"]   = np.nan
        out["pullback_depth"]  = np.nan
        out["invalidation_code"] = ""

        # Process session by session (state resets each day)
        for session_date, session in out.groupby("session_date"):
            self._process_session(out, session, session_date)

        self._print_summary(out)
        return out

    # ── Session processor ──────────────────────────────────────────────────────

    def _process_session(
        self, out: pd.DataFrame, session: pd.DataFrame, session_date
    ) -> None:
        state = _SessionState()

        for ts, row in session.iterrows():
            # ── Skip OR period and post-cutoff bars for new entries ────────────
            if row["phase"] == "OR":
                continue

            bar_time = ts.time()

            # ── Pending fill from PRIOR bar's Phase B trigger ──────────────────
            # v1.4 10.1: "Entry at NEXT bar's open [not Phase B close —
            # look-ahead bias]". The scoring decision was already made on the
            # previous bar; this bar's job is purely mechanical: fill at open.
            if state.awaiting_fill:
                out.at[ts, "signal"]         = "LONG"
                out.at[ts, "signal_price"]   = row["open"]
                out.at[ts, "signal_bar_idx"] = row["bar_index"]
                out.at[ts, "phase_b_score"]  = state.pending_score    # carried from trigger bar
                out.at[ts, "pullback_depth"] = state.pending_depth    # carried from trigger bar
                out.at[ts, "phase_a_bar_idx"] = state.phase_a_bar_idx
                state.signal_fired  = True
                state.awaiting_fill = False
                continue

            # ── If waiting for Phase B ─────────────────────────────────────────
            if state.waiting_for_b:
                state.bars_since_a += 1

                # Timeout: 5 bars elapsed without a valid entry → cancel setup
                if state.bars_since_a > self.cfg.max_pullback_bars:
                    state.phase_a_bar_idx = None   # cancel window, keep phase_a_seen=True
                    state.phase_a_close   = None
                    continue
                else:
                    # v1.4 7.1/10.1: update highest_high_since_break BEFORE
                    # checking invalidations or scoring this bar.
                    state.highest_high = max(state.highest_high, row["high"])

                    # Compute once — used by I1b invalidation AND S4 scoring below.
                    pullback_depth = self._pullback_depth(
                        row, state.highest_high, row["or_high"]
                    )

                    # Check invalidation (I1a/I1b/I2/I3/I4) — cancel setup if triggered
                    inv = self._check_invalidation(row, state, bar_time, pullback_depth)
                    if inv:
                        out.at[ts, "invalidation_code"] = inv
                        state.phase_a_bar_idx = None   # cancel window, keep phase_a_seen=True
                        state.phase_a_close   = None
                        continue
                    else:
                        # B1 (v1.4 5.6, mandatory): bar LOW must have touched
                        # OR_H from above -- confirms a genuine pullback
                        # occurred. If not touched, this bar simply isn't a
                        # valid Phase B candidate; keep waiting (not an
                        # invalidation -- the setup is still alive).
                        if row["low"] > row["or_high"]:
                            continue

                        # Score this bar as a potential Phase B entry
                        score = self._score_phase_b(row, pullback_depth, bar_time)

                        out.at[ts, "phase_b_window"]  = True
                        out.at[ts, "phase_b_score"]   = score
                        out.at[ts, "pullback_depth"]  = pullback_depth
                        out.at[ts, "phase_a_bar_idx"] = state.phase_a_bar_idx
                        out.at[ts, "phase_a_close"]   = state.phase_a_close

                        if score >= self.cfg.score_threshold and bar_time <= _14_30:
                            # Decision made -- but v1.4 requires the actual
                            # fill on the NEXT bar's open, not this bar's close.
                            state.awaiting_fill = True
                            state.pending_score = score
                            state.pending_depth = pullback_depth
                        continue   # don't double-check Phase A on a Phase B bar

            # ── Phase A detection ──────────────────────────────────────────────
            if state.signal_fired:
                continue   # one signal per session max

            if bar_time > _14_30:
                continue   # past entry cutoff

            # One Phase A attempt per session — ignore subsequent setups
            if not state.phase_a_seen and self._is_phase_a(row, bar_time):
                out.at[ts, "phase_a_active"]  = True
                out.at[ts, "phase_a_bar_idx"] = row["bar_index"]
                out.at[ts, "phase_a_close"]   = row["close"]
                state.phase_a_bar_idx = row["bar_index"]
                state.phase_a_close   = row["close"]
                state.highest_high    = row["close"]  # v1.4 7.1: init at Phase A CLOSE
                state.bars_since_a    = 0
                state.phase_a_seen    = True   # lock — no further Phase A this session

    # ── Phase A conditions ─────────────────────────────────────────────────────

    def _is_phase_a(self, row: pd.Series, bar_time: datetime.time) -> bool:
        """
        All conditions must pass simultaneously on a single bar.
        No invalidation check here — invalidations apply only AFTER Phase A fires.
        """
        if pd.isna(row["or_high"]) or pd.isna(row["atr"]):
            return False

        return (
            row["close"] > row["or_high"] * (1 + self.cfg.breakout_buffer_pct)  # A2: cleared OR_H by buffer
            and row["rvol_tod"] > self.cfg.rvol_threshold     # volume confirms
            and row["close_pos"] > self.cfg.close_pos_min     # strong close in bar
        )

    # ── Invalidation conditions (I1–I4) ───────────────────────────────────────

    def _check_invalidation(
        self, row: pd.Series, state: _SessionState, bar_time: datetime.time,
        pullback_depth: float,
    ) -> Optional[str]:
        """
        Returns the invalidation code that fired, or None if setup still valid.
        """
        # I1a: close fell back below OR high by > 0.1%
        if row["close"] < row["or_high"] * (1 - self.cfg.i1_pct):
            return "I1"

        # I1b: pullback depth exceeded 1.0 — price fully round-tripped back through
        #      or_high and beyond. Uses the SAME normalization as pullback_depth
        #      itself (relative to the breakout move: phase_a_close - or_high), NOT
        #      a price-percentage tolerance. A price-based tolerance (e.g. 0.1% of
        #      or_high) is dimensionally wrong here: when the breakout move itself is
        #      small (Phase A only requires close > or_high, not by much), a wick of
        #      just a few paisa can already push depth well past 1.0 while remaining
        #      far smaller than 0.1% of the stock's price — so a price-based check
        #      would silently never fire. Checking depth directly avoids that.
        if pd.notna(pullback_depth) and pullback_depth > 1.0:
            return "I1b"

        # I2: sector weak for 2+ consecutive bars (only after 10:15 AM)
        if bar_time >= _10_15:
            if row.get("sector_consec_below_vwap", 0) >= 2:
                return "I2"

        # I3: opposite OR boundary broken (close below or_low)
        if row["close"] < row["or_low"]:
            return "I3"

        # I4: highest_high_since_break ran > 1.5x ATR above or_high — v1.4 5.5:
        #     "Price has run too far before pulling back. Stop/target math is
        #     no longer valid from this base." Uses the TRACKED PEAK, not this
        #     bar's own close -- a bar could be pulling back right now while
        #     the earlier peak (a few bars ago) is what actually triggers this.
        if state.highest_high > row["or_high"] + self.cfg.i4_atr_mult * row["atr"]:
            return "I4"

        return None

    # ── Phase B scoring ────────────────────────────────────────────────────────

    def _score_phase_b(
        self, row: pd.Series, pullback_depth: float, bar_time: datetime.time
    ) -> int:
        score = 0
        cfg   = self.cfg

        # S1: close > VWAP = +2 (only after 10:15 AM when VWAP is meaningful)
        s1_pass = False
        if bar_time >= _10_15 and row["close"] > row["vwap"]:
            score  += 2
            s1_pass = True

            # S2: within 1% of VWAP = +1 (ONLY if S1 passed — near-VWAP pullback)
            vwap_dist = abs(row["close"] - row["vwap"]) / row["vwap"]
            if vwap_dist <= cfg.s2_vwap_pct:
                score += 1

        # S3: sector index moving in same direction (above its VWAP) = +2
        if row.get("sector_above_vwap", False):
            score += 2

        # S4: pullback depth < 50% of Phase A move = +1 (shallow pullback = strength)
        if pd.notna(pullback_depth) and pullback_depth < cfg.s4_pullback_max:
            score += 1

        # S5: positive bar body (close >= open) = +1
        if row["close"] >= row["open"]:
            score += 1

        # S6: close above previous day high = +1
        if pd.notna(row.get("prev_day_high")) and row["close"] > row["prev_day_high"]:
            score += 1

        return score

    # ── Pullback depth ─────────────────────────────────────────────────────────

    @staticmethod
    def _pullback_depth(
        row: pd.Series, highest_high: float, or_high: float
    ) -> float:
        """
        How deeply did price pull back from its peak since Phase A?

        v1.4 7.2: uses highest_high_since_break (the running peak reached
        AFTER Phase A fired), NOT the Phase A bar's own close — if price
        keeps running before pulling back, the true pullback is measured
        from that higher peak, not from where Phase A happened to trigger.

        depth = (highest_high - bar_low) / (highest_high - or_high)

        0.0 = no pullback (bar stayed at the peak)
        0.5 = pulled back halfway into the breakout move
        1.0 = fully retraced back to or_high

        Guaranteed to be in [0, 1] for any bar that reaches the OUTPUT (scored) —
        I1b invalidates the bar (and never scores it) as soon as this value is
        computed to be > 1.0.
        """
        move = highest_high - or_high
        if move <= 0:
            return np.nan
        return (highest_high - row["low"]) / move

    # ── Validation ─────────────────────────────────────────────────────────────

    @staticmethod
    def _validate_input(df: pd.DataFrame) -> None:
        required = {
            "phase", "bar_index", "close", "open", "high", "low",
            "or_high", "or_low", "rvol_tod", "close_pos", "vwap",
            "atr", "sector_above_vwap", "sector_consec_below_vwap",
            "prev_day_high", "session_date",
        }
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"[Signal] Missing columns from SFE: {missing}")

    # ── Summary ────────────────────────────────────────────────────────────────

    @staticmethod
    def _print_summary(df: pd.DataFrame) -> None:
        sessions   = df["session_date"].nunique()
        phase_a    = df["phase_a_active"].sum()
        signals    = (df["signal"] == "LONG").sum()
        b_attempts = df["phase_b_window"].sum()

        print(f"\n{'='*55}")
        print(f"[Signal] model_version=1.3 (v1.4 spec fixes: A2 buffer, B1 gate, highest_high tracking, next-bar-open fill)")
        print(f"[Signal] {sessions} sessions  |  "
              f"{phase_a} Phase A setups  |  "
              f"{b_attempts} Phase B bars  |  "
              f"{signals} LONG signals")

        inv_counts = df.loc[df["invalidation_code"] != "", "invalidation_code"].value_counts()
        print(f"\n[Signal] Invalidations fired (I1a/I1b/I2/I3/I4):")
        if len(inv_counts) > 0:
            for code, cnt in inv_counts.items():
                print(f"  {code}: {cnt}")
        else:
            print("  none")

        max_depth = df["pullback_depth"].max()
        print(f"\n[Signal] Max pullback_depth across all Phase B bars: {max_depth:.3f}  "
              f"(should be <= 1.0 — if not, I1b is not active)")

        if signals > 0:
            sig_df = df[df["signal"] == "LONG"][[
                "session_date", "signal_bar_idx", "signal_price",
                "phase_b_score", "phase_a_bar_idx", "pullback_depth",
            ]].copy()
            sig_df["pullback_depth"] = sig_df["pullback_depth"].round(3)
            print(f"\n[Signal] All LONG entries:")
            print(sig_df.to_string(index=False))

        print(f"\n[Signal] Next step: IntradayRiskManager")
        print(f"{'='*55}")


# ── CLI test ───────────────────────────────────────────────────────────────────