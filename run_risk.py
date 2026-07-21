"""
run_risk.py — Step 4 validation: size + simulate every LONG signal from Step 3.

Logic lives in core1/data.py + core1/features.py + core1/signals.py + core1/risk.py.
This file is orchestration and prints only — paste this file when Step 4
output needs debugging.
"""

from config import CREDENTIALS, SETTINGS
from core.data import AngelDataPipeline, InstrumentConfig
from core.features import SessionFeatureEngineer
from core.signals import TwoPhaseSignalModel, SignalConfig
from core.risk import IntradayRiskManager, RiskConfig, DailyRiskTracker

if __name__ == "__main__":
    # ── Fetch + features + signals ────────────────────────────────────────────
    pipeline = AngelDataPipeline(**CREDENTIALS, cache_dir=SETTINGS.cache_dir)
    config   = InstrumentConfig.HDFCBANK()

    print("=" * 55)
    print("Fetching HDFCBANK + BANKNIFTY sector data...")
    print("=" * 55)
    stock_df, sector_df = pipeline.fetch_with_sector(config, days=30)

    sfe         = SessionFeatureEngineer(or_end_bar=SETTINGS.or_end_bar)
    features_df = sfe.compute(stock_df, sector_df, config)

    model      = TwoPhaseSignalModel(SignalConfig.from_settings(SETTINGS))
    signals_df = model.generate(features_df)

    # ── Size + simulate every signal ──────────────────────────────────────────
    risk_mgr = IntradayRiskManager(RiskConfig.from_settings(SETTINGS))
    tracker  = DailyRiskTracker(SETTINGS.daily_loss_limit)

    long_signals = signals_df[signals_df["signal"] == "LONG"].copy()

    print(f"\n{'='*55}")
    print(f"[Risk] model_version=1.0  |  capital=₹{SETTINGS.capital:,.0f}  |  "
          f"risk_per_trade=₹{SETTINGS.capital * SETTINGS.risk_per_trade_pct:,.0f} "
          f"({SETTINGS.risk_per_trade_pct:.0%})")
    print(f"[Risk] {len(long_signals)} signals to size + simulate")

    results = []
    skipped = []

    for ts, sig_row in long_signals.iterrows():
        session_date = sig_row["session_date"]
        session_bars = features_df[features_df["session_date"] == session_date]

        setup = risk_mgr.size_trade(
            entry_price=sig_row["close"], atr=sig_row["atr"], entry_time=ts
        )

        if not setup.is_tradeable:
            skipped.append((session_date, setup.skip_reason))
            continue

        if not tracker.can_trade():
            skipped.append((session_date, "daily loss limit already hit"))
            continue

        result = risk_mgr.simulate_trade(setup, session_bars)
        tracker.record(result)
        results.append((session_date, setup, result))

    # ── Report ─────────────────────────────────────────────────────────────────
    print(f"\n[Risk] {len(results)} trades executed  |  {len(skipped)} skipped")

    if skipped:
        print(f"\n[Risk] Skipped signals:")
        for date, reason in skipped:
            print(f"  {date}: {reason}")

    if results:
        print(f"\n[Risk] Trade log:")
        header = (f"{'date':<12} {'entry':>8} {'stop':>8} {'target':>8} "
                   f"{'qty':>5} {'exit':>8} {'reason':<11} {'trail':<6} "
                   f"{'net_pnl':>10} {'net_%':>8}")
        print(header)
        print("-" * len(header))
        for date, setup, result in results:
            print(
                f"{str(date):<12} {setup.entry_price:>8.2f} {setup.stop_price:>8.2f} "
                f"{setup.target_price:>8.2f} {setup.quantity:>5d} "
                f"{result.exit_price:>8.2f} {result.exit_reason:<11} "
                f"{str(result.trailed):<6} {result.net_pnl:>10.2f} "
                f"{result.net_pnl_pct:>7.2%}"
            )

        total_pnl = tracker.cumulative_pnl
        wins      = sum(1 for _, _, r in results if r.net_pnl > 0)
        win_rate  = wins / len(results) * 100

        print(f"\n[Risk] Summary: {len(results)} trades  |  "
              f"{wins} wins ({win_rate:.1f}% win rate)  |  "
              f"total net P&L = ₹{total_pnl:,.2f}")
        print(f"[Risk] Daily loss limit hit: {tracker.limit_hit}")

    print(f"\n[Risk] Next step: IntradayBacktestEngine")
    print("=" * 55)