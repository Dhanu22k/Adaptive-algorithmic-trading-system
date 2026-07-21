"""
run_features.py — Step 2 validation: SessionFeatureEngineer output on HDFCBANK.

Logic lives in core1/data.py + core1/features.py. This file is orchestration
and prints only — paste this file when Step 2 output needs debugging.
"""

from config import CREDENTIALS, SETTINGS
from core.data import AngelDataPipeline, InstrumentConfig
from core.features import SessionFeatureEngineer

if __name__ == "__main__":
    # ── Fetch data (uses cache if fresh) ──
    pipeline = AngelDataPipeline(**CREDENTIALS, cache_dir=SETTINGS.cache_dir)
    config   = InstrumentConfig.HDFCBANK()

    print("=" * 55)
    print("Fetching HDFCBANK + BANKNIFTY sector data...")
    print("=" * 55)
    stock_df, sector_df = pipeline.fetch_with_sector(config, days=30)

    # ── Compute features ──
    sfe = SessionFeatureEngineer(or_end_bar=SETTINGS.or_end_bar)   # ATR_PERIOD=14 daily (class constant)
    features_df = sfe.compute(stock_df, sector_df, config)

    # ── Show one full session — last trading day ──
    last_session = features_df["session_date"].max()
    day_df = features_df[features_df["session_date"] == last_session]

    print(f"\n--- Full session sample: {last_session} ({len(day_df)} bars) ---")

    # OR period
    print(f"\n[OR bars] or_high={day_df['or_high'].iloc[-1]:.2f}  "
          f"or_low={day_df['or_low'].iloc[-1]:.2f}  "
          f"or_range={day_df['or_range'].iloc[-1]:.2f}")

    # First/last ACTIVE bars
    active_day = day_df[day_df["phase"] == "ACTIVE"]
    display_cols = [
        "bar_index", "phase", "close", "close_pos",
        "or_high", "above_or_high",
        "vwap", "above_vwap",
        "sector_above_vwap", "sector_consec_below_vwap",
        "atr", "rvol_tod", "gap_regime", "prev_day_high",
    ]
    print(f"\n[ACTIVE bars — first 5]")
    print(active_day[display_cols].head(5).to_string())

    print(f"\n[ACTIVE bars — last 5]")
    print(active_day[display_cols].tail(5).to_string())

    print(f"\nTotal feature columns ({len(features_df.columns)}): {list(features_df.columns)}")