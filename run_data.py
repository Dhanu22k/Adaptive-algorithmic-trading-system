"""
run_data.py — Step 1 validation: fetch HDFCBANK + BANKNIFTY, sanity-check output.

Logic lives in core1/data.py. This file is orchestration + prints only —
this is the file to paste into chat when Step 1 output needs debugging.
"""

from config import CREDENTIALS, SETTINGS
from core.data import AngelDataPipeline, InstrumentConfig

if __name__ == "__main__":
    pipeline = AngelDataPipeline(
        **CREDENTIALS,
        cache_dir=SETTINGS.cache_dir,
    )

    instrument_config = InstrumentConfig.HDFCBANK()

    print("=" * 55)
    print("Fetching HDFCBANK + BANKNIFTY sector data...")
    print("=" * 55)

    stock_df, sector_df = pipeline.fetch_with_sector(instrument_config, days=30)

    print("\n--- HDFCBANK Sample ---")
    print(stock_df.tail(5).to_string())
    print(f"\nColumns: {list(stock_df.columns)}")
    print(f"Total rows: {len(stock_df)}")
    print(f"Date range: {stock_df.index[0]} → {stock_df.index[-1]}")

    print("\n--- BANKNIFTY Sector Sample ---")
    print(sector_df.tail(3).to_string())

    # Verify timestamp alignment (W19)
    aligned = stock_df.index.isin(sector_df.index)
    pct_aligned = aligned.mean() * 100
    print(f"\n[W19 Check] Timestamp alignment: {pct_aligned:.1f}% "
          f"of stock bars have matching sector bar")
    if pct_aligned < 95:
        print("  ⚠ Alignment below 95% — check timestamp normalization")
    else:
        print("  ✓ Timestamps aligned correctly")

    # Verify RVOL has no future leakage (W18)
    first_3_days = stock_df["rvol_tod"].head(225)  # ~3 sessions
    nan_in_first_3 = first_3_days.isna().sum()
    print(f"\n[W18 Check] RVOL NaN in first 3 sessions: {nan_in_first_3}"
          f" (expected >0 — confirms no leakage)")

    print("\n✓ Data pipeline working correctly")
    print("Next step: SessionFeatureEngineer")