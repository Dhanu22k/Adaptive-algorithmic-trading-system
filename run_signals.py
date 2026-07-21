"""
run_signals.py — Step 3 validation: TwoPhaseSignalModel output on HDFCBANK.

Logic lives in core1/data.py + core1/features.py + core1/signals.py. This file
is orchestration and prints only — paste this file when Step 3 output needs
debugging.
"""

from config import CREDENTIALS, SETTINGS
from core.data import AngelDataPipeline, InstrumentConfig
from core.features import SessionFeatureEngineer
from core.signals import TwoPhaseSignalModel, SignalConfig

if __name__ == "__main__":
    # ── Fetch + feature engineering ───────────────────────────────────────────
    pipeline = AngelDataPipeline(**CREDENTIALS, cache_dir=SETTINGS.cache_dir)
    config   = InstrumentConfig.HDFCBANK()

    print("=" * 55)
    print("Fetching HDFCBANK + BANKNIFTY sector data...")
    print("=" * 55)
    stock_df, sector_df = pipeline.fetch_with_sector(config, days=30)

    sfe         = SessionFeatureEngineer(or_end_bar=SETTINGS.or_end_bar)
    features_df = sfe.compute(stock_df, sector_df, config)

    # ── Generate signals ──────────────────────────────────────────────────────
    model      = TwoPhaseSignalModel(SignalConfig.from_settings(SETTINGS))
    signals_df = model.generate(features_df)

    # ── Inspect one session with a signal (if any) ────────────────────────────
    signal_sessions = signals_df[signals_df["signal"] == "LONG"]["session_date"].unique()

    if len(signal_sessions) > 0:
        day = signal_sessions[0]
        day_df = signals_df[signals_df["session_date"] == day]
        print(f"\n--- Session with signal: {day} ---")
        show_cols = [
            "bar_index", "phase", "close", "rvol_tod", "close_pos",
            "above_or_high", "phase_a_active", "phase_b_window",
            "phase_b_score", "pullback_depth", "signal",
        ]
        active = day_df[day_df["phase"].isin(["ACTIVE", "CUTOFF"])]
        print(active[show_cols].to_string())
    else:
        print("\n[Signal] No signals in this 30-day window — check thresholds or extend history.")

    # ── Phase A without Phase B (missed entries) ──────────────────────────────
    phase_a_days  = signals_df[signals_df["phase_a_active"]]["session_date"].unique()
    signal_days   = set(signal_sessions)
    missed        = [d for d in phase_a_days if d not in signal_days]
    print(f"\n[Signal] Phase A setups that didn't reach score >= {SETTINGS.score_threshold}: "
          f"{len(missed)} session(s): {missed}")