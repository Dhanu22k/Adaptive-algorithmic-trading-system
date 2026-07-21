"""
run_backtest.py — Step 5 validation: full multi-instrument backtest.

Logic lives in core1/data.py + core1/features.py + core1/signals.py +
core1/risk.py + core1/backtest.py. This file is orchestration and prints
only — paste this file when Step 5 output needs debugging.
"""

from config import CREDENTIALS, SETTINGS
from core.data import AngelDataPipeline
from core.features import SessionFeatureEngineer
from core.signals import TwoPhaseSignalModel, SignalConfig
from core.risk import IntradayRiskManager, RiskConfig
from core.backtest import IntradayBacktestEngine

if __name__ == "__main__":
    pipeline     = AngelDataPipeline(**CREDENTIALS, cache_dir=SETTINGS.cache_dir)
    sfe          = SessionFeatureEngineer(or_end_bar=SETTINGS.or_end_bar)
    signal_model = TwoPhaseSignalModel(SignalConfig.from_settings(SETTINGS))
    risk_manager = IntradayRiskManager(RiskConfig.from_settings(SETTINGS))

    engine = IntradayBacktestEngine(
        pipeline=pipeline, sfe=sfe, signal_model=signal_model,
        risk_manager=risk_manager, daily_loss_limit=SETTINGS.daily_loss_limit,
        history_days=SETTINGS.history_days,
    )

    report = engine.run(SETTINGS.instruments)

    # ── Report ─────────────────────────────────────────────────────────────────
    print(f"\n{'='*55}")
    print(f"[Backtest] model_version=1.0")
    print(f"[Backtest] {report.total_trades} trades executed  |  {len(report.skipped)} skipped")

    if report.skipped:
        print(f"\n[Backtest] Skipped signals:")
        for ticker, date, reason in report.skipped:
            print(f"  {ticker} {date}: {reason}")

    if report.trades:
        print(f"\n[Backtest] Full trade log (chronological):")
        header = (f"{'date':<12} {'ticker':<10} {'entry':>8} {'exit':>8} "
                   f"{'qty':>5} {'reason':<11} {'trail':<6} {'net_pnl':>10} "
                   f"{'net_%':>8} {'cum_pnl':>10}")
        print(header)
        print("-" * len(header))
        cum = 0.0
        for trade, cum_pnl in zip(report.trades, report.equity_curve):
            r, s = trade.result, trade.setup
            print(
                f"{str(s.entry_time.date()):<12} {trade.ticker:<10} {s.entry_price:>8.2f} "
                f"{r.exit_price:>8.2f} {s.quantity:>5d} {r.exit_reason:<11} "
                f"{str(r.trailed):<6} {r.net_pnl:>10.2f} {r.net_pnl_pct:>7.2%} "
                f"{cum_pnl:>10.2f}"
            )

        print(f"\n[Backtest] Overall: {report.total_trades} trades  |  "
              f"win_rate={report.win_rate:.1%}  |  "
              f"profit_factor={report.profit_factor:.2f}  |  "
              f"total_net_pnl=₹{report.total_net_pnl:,.2f}  |  "
              f"max_drawdown=₹{report.max_drawdown:,.2f}")

        print(f"\n[Backtest] Exit reason distribution:")
        for reason, count in sorted(report.exit_reason_counts.items(), key=lambda x: -x[1]):
            pct = count / report.total_trades * 100
            print(f"  {reason:<11} {count:>3d}  ({pct:.0f}%)")

        print(f"\n[Backtest] Per-instrument breakdown:")
        for ticker, stats in sorted(report.per_instrument_stats.items()):
            print(f"  {ticker:<10} trades={stats['trades']:>3d}  "
                  f"win_rate={stats['win_rate']:.1%}  net_pnl=₹{stats['net_pnl']:,.2f}")

        # ── Phase 1 go/no-go check (architecture spec: win rate > 58%, 80+ trades) ──
        print(f"\n[Backtest] Phase 1 validation targets (architecture v1.4):")
        print(f"  Trades:   {report.total_trades} / 80+  "
              f"{'✓' if report.total_trades >= 80 else '— need more history/instruments'}")
        print(f"  Win rate: {report.win_rate:.1%} / >58%  "
              f"{'✓' if report.win_rate > 0.58 else '— below target'}")
    else:
        print("\n[Backtest] No trades executed across any instrument.")

    print(f"\n[Backtest] Next step: PerformanceAnalyzer")
    print("=" * 55)