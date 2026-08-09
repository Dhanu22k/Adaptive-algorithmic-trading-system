"""
debug_reproducibility.py — ONE-TIME diagnostic, not part of the pipeline.

Run this script TWICE IN A ROW, with no other changes in between (don't
delete cache, don't edit any files). Compare the two "FEATURES HASH" lines
it prints.

  - If the hash is IDENTICAL both times  -> SessionFeatureEngineer is
    deterministic; the bug must be inside TwoPhaseSignalModel's scoring.
  - If the hash DIFFERS between the two runs -> SessionFeatureEngineer
    itself is non-deterministic on identical cached input, which is the
    more surprising (and more important) finding.

Either way, paste BOTH runs' full output back — this will tell us
definitively where to look next instead of guessing further.
"""

from config import CREDENTIALS, SETTINGS
from core.data import AngelDataPipeline, InstrumentConfig
from core.features import SessionFeatureEngineer
from core.signals import TwoPhaseSignalModel, SignalConfig
import pandas as pd
import hashlib

pipeline = AngelDataPipeline(**CREDENTIALS, cache_dir=SETTINGS.cache_dir)
config   = InstrumentConfig.ICICIBANK()

# use_cache=True deliberately — we want to see if identical cached input
# produces identical output, not test the fetch layer again
stock_df, sector_df = pipeline.fetch_with_sector(config, days=30)

sfe = SessionFeatureEngineer(or_end_bar=SETTINGS.or_end_bar)
features_df = sfe.compute(stock_df, sector_df, config)

# ── Fingerprint the ENTIRE features_df — deterministic if and only if
#    every value in every column is byte-identical to a prior run ──
row_hashes = pd.util.hash_pandas_object(features_df, index=True)
full_hash = hashlib.sha256(row_hashes.values.tobytes()).hexdigest()
print(f"\n{'='*60}")
print(f"FEATURES HASH (full features_df): {full_hash}")
print(f"{'='*60}\n")

# ── Zoom into the specific problem bar: 2026-06-25, bar_index=33 ──
import datetime
target_date = datetime.date(2026, 6, 25)
day_df = features_df[features_df["session_date"] == target_date]
bar33 = day_df[day_df["bar_index"] == 33]

print("--- 2026-06-25 bar_index=33 — ALL feature values ---")
if len(bar33) == 0:
    print("  Bar 33 not found for this date in this run's data window.")
else:
    for col in features_df.columns:
        print(f"  {col:<28} {bar33[col].values[0]}")

# ── Also fingerprint just the columns that feed scoring, for a tighter check ──
score_inputs = ["close", "vwap", "sector_above_vwap", "sector_close",
                 "sector_vwap", "prev_day_high", "open", "or_high", "atr"]
score_cols_present = [c for c in score_inputs if c in bar33.columns]
print(f"\n--- Score-relevant inputs for bar 33 ---")
for c in score_cols_present:
    print(f"  {c:<20} {bar33[c].values[0]!r}")

# ── Now run the signal model and show the score for this exact bar ──
model = TwoPhaseSignalModel(SignalConfig.from_settings(SETTINGS))
signals_df = model.generate(features_df)
sig_bar33 = signals_df[(signals_df["session_date"] == target_date) &
                         (signals_df["bar_index"] == 33)]
print(f"\n--- Computed score for bar 33 in THIS run ---")
print(f"  phase_b_score = {sig_bar33['phase_b_score'].values[0] if len(sig_bar33) else 'N/A'}")
print(f"  pullback_depth = {sig_bar33['pullback_depth'].values[0] if len(sig_bar33) else 'N/A'}")