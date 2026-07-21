"""
run_performance.py — Step 6 validation: statistical performance analysis
on top of the Step 5 backtest.

Logic lives in core1/*.py. This file is orchestration and prints only.
"""

from config import CREDENTIALS, SETTINGS
from core.data import AngelDataPipeline
from core.features import SessionFeatureEngineer
from core.signals import TwoPhaseSignalModel, SignalConfig
from core.risk import IntradayRiskManager, RiskConfig
from core.backtest import IntradayBacktestEngine
from core.performance import PerformanceAnalyzer

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

    # NOTE: hardcoded to the 3 confirmed-working instruments until INFY/TCS's
    # sector proxy is fixed (see find_it_futures.py). Swap back to
    # SETTINGS.instruments once that's resolved.
    tickers = ["HDFCBANK", "ICICIBANK", "RELIANCE"]
    report  = engine.run(tickers)

    analyzer = PerformanceAnalyzer()
    perf     = analyzer.analyze(report)

    print(f"\n{'='*55}")
    print(f"[Performance] model_version=1.0")
    print(f"[Performance] {perf.trade_count} trades analyzed")

    if perf.trade_count == 0:
        print("[Performance] No trades to analyze.")
    else:
        print(f"\n[Performance] Win rate: {perf.win_rate:.1%}  "
              f"(95% CI: {perf.win_rate_ci_95[0]:.1%} – {perf.win_rate_ci_95[1]:.1%})")
        print(f"[Performance] Avg win: ₹{perf.avg_win:,.2f}  |  "
              f"Avg loss: ₹{perf.avg_loss:,.2f}  |  "
              f"Win/loss ratio: {perf.win_loss_ratio:.2f}")
        print(f"[Performance] Expectancy per trade: ₹{perf.expectancy_per_trade:,.2f}")
        print(f"[Performance] Profit factor: {perf.profit_factor:.2f}")
        print(f"[Performance] Sharpe-like ratio (per-trade, NOT annualized): {perf.sharpe_like:.2f}")
        print(f"[Performance] Sortino-like ratio (per-trade, NOT annualized): {perf.sortino_like:.2f}")
        print(f"[Performance] Max consecutive wins: {perf.max_consecutive_wins}  |  "
              f"Max consecutive losses: {perf.max_consecutive_losses}")

        print(f"\n[Performance] Daily P&L ({len(perf.daily_pnl)} trading days):")
        print(f"  Best day:  {perf.best_day[0]}  ₹{perf.best_day[1]:,.2f}")
        print(f"  Worst day: {perf.worst_day[0]}  ₹{perf.worst_day[1]:,.2f}")
        print(f"  % losing days: {perf.pct_losing_days:.1%}")

        print(f"\n[Performance] P&L by exit reason:")
        for reason, stats in sorted(perf.exit_reason_pnl.items(), key=lambda x: -x[1]["count"]):
            print(f"  {reason:<11} count={stats['count']:>3d}  "
                  f"avg_pnl=₹{stats['avg_pnl']:>8.2f}  total_pnl=₹{stats['total_pnl']:>9.2f}")

        print(f"\n[Performance] Kelly-suggested risk fraction: {perf.kelly_fraction:.2%}  "
              f"(currently using {SETTINGS.risk_per_trade_pct:.0%} — "
              f"{'consider reducing' if perf.kelly_fraction < SETTINGS.risk_per_trade_pct else 'current sizing is conservative relative to Kelly'})")

        print(f"\n[Performance] Phase 1 verdict: {perf.phase1_verdict}")
        print(f"  {perf.phase1_reasoning}")

    print(f"\n[Performance] Next step: Config layer (YAML) / Paper trading prep")
    print("=" * 55)