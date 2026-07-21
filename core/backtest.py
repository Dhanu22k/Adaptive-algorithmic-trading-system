"""
core1/backtest.py — Step 5: IntradayBacktestEngine
=====================================================
Runs the full pipeline (data -> features -> signals -> risk) across MULTIPLE
instruments and aggregates the results into one trade log with proper
cross-instrument risk semantics.

Key design decision: the daily loss limit is SHARED across all instruments,
not per-instrument. This is one account with one Rs 1L capital pool, so if
HDFCBANK stops out for Rs 1,000 in the morning, ICICIBANK trading later that
SAME day only has Rs 1,000 of daily budget left — not a fresh Rs 2,000.
To enforce this correctly, all instruments' signals are merged into a single
chronological timeline and walked in date order, resetting the tracker at
each calendar-day boundary.

Concurrent positions across DIFFERENT instruments on the same day ARE
allowed (e.g. HDFCBANK and ICICIBANK both entering the same morning) — the
25%-of-capital position cap already implies multiple simultaneous positions
were anticipated (2 x 25% = 50% deployed is within the architecture's stated
limits). This is a modeling assumption, not an explicit architecture rule —
worth revisiting once real trade volume makes concurrency common.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.data import AngelDataPipeline, InstrumentConfig
from core.features import SessionFeatureEngineer
from core.signals import TwoPhaseSignalModel
from core.risk import IntradayRiskManager, DailyRiskTracker, TradeSetup, TradeResult


@dataclass
class BacktestTrade:
    """One executed trade, tagged with which instrument it came from."""
    ticker:  str
    setup:   TradeSetup
    result:  TradeResult


@dataclass
class BacktestReport:
    trades:  list           # list[BacktestTrade]
    skipped: list            # list[(ticker, session_date, reason)]
    signals_by_instrument: dict   # ticker -> signals_df (for debugging)

    # ── Aggregate stats, computed lazily ──────────────────────────────────────

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def total_net_pnl(self) -> float:
        return sum(t.result.net_pnl for t in self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.result.net_pnl > 0)
        return wins / len(self.trades)

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t.result.net_pnl for t in self.trades if t.result.net_pnl > 0)
        gross_loss   = -sum(t.result.net_pnl for t in self.trades if t.result.net_pnl < 0)
        return gross_profit / gross_loss if gross_loss > 0 else float("inf")

    @property
    def exit_reason_counts(self) -> dict:
        counts = {}
        for t in self.trades:
            counts[t.result.exit_reason] = counts.get(t.result.exit_reason, 0) + 1
        return counts

    @property
    def per_instrument_stats(self) -> dict:
        stats = {}
        for ticker in {t.ticker for t in self.trades}:
            tks = [t for t in self.trades if t.ticker == ticker]
            wins = sum(1 for t in tks if t.result.net_pnl > 0)
            stats[ticker] = {
                "trades": len(tks),
                "wins": wins,
                "win_rate": wins / len(tks) if tks else 0.0,
                "net_pnl": sum(t.result.net_pnl for t in tks),
            }
        return stats

    @property
    def equity_curve(self) -> list:
        """Cumulative net P&L after each trade, in chronological order."""
        curve = []
        running = 0.0
        for t in self.trades:
            running += t.result.net_pnl
            curve.append(running)
        return curve

    @property
    def max_drawdown(self) -> float:
        """Largest peak-to-trough decline in the equity curve (in rupees)."""
        curve = self.equity_curve
        if not curve:
            return 0.0
        peak = curve[0]
        max_dd = 0.0
        for v in curve:
            peak = max(peak, v)
            max_dd = min(max_dd, v - peak)
        return max_dd


class IntradayBacktestEngine:
    """
    Usage:
        engine = IntradayBacktestEngine(pipeline, sfe, signal_model, risk_manager, settings)
        report = engine.run(["HDFCBANK", "ICICIBANK", "RELIANCE", "INFY", "TCS"])
    """

    def __init__(
        self,
        pipeline: AngelDataPipeline,
        sfe: SessionFeatureEngineer,
        signal_model: TwoPhaseSignalModel,
        risk_manager: IntradayRiskManager,
        daily_loss_limit: float,
        history_days: int = 30,
    ) -> None:
        self.pipeline         = pipeline
        self.sfe              = sfe
        self.signal_model     = signal_model
        self.risk_manager     = risk_manager
        self.daily_loss_limit = daily_loss_limit
        self.history_days     = history_days

    def run(self, tickers: list) -> BacktestReport:
        # ── Phase A: fetch + features + signals, per instrument ────────────────
        features_by_ticker = {}
        signals_by_ticker  = {}
        all_signals = []   # list of (entry_ts, ticker, signal_row)

        for ticker in tickers:
            try:
                config = InstrumentConfig.from_ticker(ticker)
                print(f"\n{'='*55}\n[Backtest] {ticker} — fetching + computing...\n{'='*55}")

                stock_df, sector_df = self.pipeline.fetch_with_sector(
                    config, days=self.history_days
                )
                features_df = self.sfe.compute(stock_df, sector_df, config)
                signals_df  = self.signal_model.generate(features_df)

                features_by_ticker[ticker] = features_df
                signals_by_ticker[ticker]  = signals_df

                long_signals = signals_df[signals_df["signal"] == "LONG"]
                for ts, row in long_signals.iterrows():
                    all_signals.append((ts, ticker, row))

            except Exception as exc:
                print(f"[Backtest] ⚠ {ticker} failed: {exc}")
                print(f"[Backtest]   Skipping {ticker}, continuing with remaining instruments.")
                continue

        # ── Phase B: merge chronologically, walk with a SHARED daily tracker ───
        all_signals.sort(key=lambda x: x[0])

        tracker = DailyRiskTracker(self.daily_loss_limit)
        current_date = None
        trades: list = []
        skipped: list = []

        for ts, ticker, sig_row in all_signals:
            session_date = sig_row["session_date"]

            if session_date != current_date:
                tracker.reset()
                current_date = session_date

            if not tracker.can_trade():
                skipped.append((ticker, session_date, "daily loss limit already hit"))
                continue

            setup = self.risk_manager.size_trade(
                entry_price=sig_row["close"], atr=sig_row["atr"], entry_time=ts
            )

            if not setup.is_tradeable:
                skipped.append((ticker, session_date, setup.skip_reason))
                continue

            session_bars = features_by_ticker[ticker]
            session_bars = session_bars[session_bars["session_date"] == session_date]

            result = self.risk_manager.simulate_trade(setup, session_bars)
            tracker.record(result)
            trades.append(BacktestTrade(ticker=ticker, setup=setup, result=result))

        return BacktestReport(
            trades=trades, skipped=skipped, signals_by_instrument=signals_by_ticker
        )