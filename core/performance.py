"""
core1/performance.py — Step 6: PerformanceAnalyzer
=====================================================
Takes a BacktestReport (Step 5) and produces statistically grounded metrics
for the Phase 1 go/no-go decision — not just point estimates.

Why this exists separately from BacktestReport's basic stats: with only
17 trades (current sample), a raw win_rate=35.3% is nearly meaningless on
its own — the confidence interval around it is wide enough that the true
win rate could plausibly be much higher OR lower. This module makes that
uncertainty explicit instead of letting a single number look more solid
than it is.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class PerformanceReport:
    trade_count:        int
    win_rate:           float
    win_rate_ci_95:      tuple            # (lower, upper) Wilson score interval
    avg_win:             float
    avg_loss:            float             # negative number
    win_loss_ratio:      float             # avg_win / abs(avg_loss)
    expectancy_per_trade: float
    profit_factor:        float
    sharpe_like:           float            # mean/std of per-trade net_pnl_pct — NOT annualized
    sortino_like:           float           # mean / downside-std of per-trade net_pnl_pct
    max_consecutive_wins:   int
    max_consecutive_losses: int
    daily_pnl:               dict           # date -> net_pnl summed
    best_day:                 tuple         # (date, pnl)
    worst_day:                 tuple        # (date, pnl)
    pct_losing_days:            float
    exit_reason_pnl:              dict      # reason -> {count, avg_pnl, total_pnl}
    kelly_fraction:                float    # suggested risk fraction per Kelly criterion
    phase1_verdict:                 str
    phase1_reasoning:                str


class PerformanceAnalyzer:
    """
    Usage:
        analyzer = PerformanceAnalyzer()
        perf     = analyzer.analyze(backtest_report)
    """

    MIN_TRADES_FOR_VERDICT = 30   # below this, any win-rate conclusion is noise
    TARGET_WIN_RATE = 0.58        # from architecture v1.4 Phase 1 spec
    TARGET_TRADE_COUNT = 80       # from architecture v1.4 Phase 1 spec

    def analyze(self, report) -> PerformanceReport:
        trades = report.trades
        n = len(trades)

        if n == 0:
            return self._empty_report()

        pnls     = [t.result.net_pnl for t in trades]
        pnl_pcts = [t.result.net_pnl_pct for t in trades]
        wins     = [p for p in pnls if p > 0]
        losses   = [p for p in pnls if p <= 0]

        win_rate = len(wins) / n
        avg_win  = float(np.mean(wins)) if wins else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0

        win_loss_ratio = (avg_win / abs(avg_loss)) if avg_loss != 0 else float("inf")
        expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss

        ci_lower, ci_upper = self._wilson_ci(len(wins), n)

        sharpe_like  = self._risk_adjusted_ratio(pnl_pcts, downside_only=False)
        sortino_like = self._risk_adjusted_ratio(pnl_pcts, downside_only=True)

        max_win_streak, max_loss_streak = self._streaks(pnls)

        daily_pnl = self._daily_pnl(trades)
        best_day  = max(daily_pnl.items(), key=lambda x: x[1]) if daily_pnl else (None, 0.0)
        worst_day = min(daily_pnl.items(), key=lambda x: x[1]) if daily_pnl else (None, 0.0)
        pct_losing_days = (
            sum(1 for v in daily_pnl.values() if v < 0) / len(daily_pnl)
            if daily_pnl else 0.0
        )

        exit_reason_pnl = self._exit_reason_breakdown(trades)

        kelly = self._kelly_fraction(win_rate, win_loss_ratio)

        verdict, reasoning = self._phase1_verdict(n, win_rate, ci_lower, ci_upper)

        return PerformanceReport(
            trade_count=n, win_rate=win_rate, win_rate_ci_95=(ci_lower, ci_upper),
            avg_win=avg_win, avg_loss=avg_loss, win_loss_ratio=win_loss_ratio,
            expectancy_per_trade=expectancy, profit_factor=report.profit_factor,
            sharpe_like=sharpe_like, sortino_like=sortino_like,
            max_consecutive_wins=max_win_streak, max_consecutive_losses=max_loss_streak,
            daily_pnl=daily_pnl, best_day=best_day, worst_day=worst_day,
            pct_losing_days=pct_losing_days, exit_reason_pnl=exit_reason_pnl,
            kelly_fraction=kelly, phase1_verdict=verdict, phase1_reasoning=reasoning,
        )

    # ── Statistical helpers ────────────────────────────────────────────────────

    @staticmethod
    def _wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple:
        """
        Wilson score interval — more reliable than a normal approximation for
        small samples or win rates near 0 or 1. Returns (lower, upper) at 95%
        confidence by default.
        """
        if n == 0:
            return (0.0, 0.0)
        p_hat = wins / n
        denom = 1 + z**2 / n
        center = (p_hat + z**2 / (2 * n)) / denom
        margin = (z * math.sqrt(p_hat * (1 - p_hat) / n + z**2 / (4 * n**2))) / denom
        return (max(0.0, center - margin), min(1.0, center + margin))

    @staticmethod
    def _risk_adjusted_ratio(pnl_pcts: list, downside_only: bool) -> float:
        """
        Mean-over-std ratio of per-trade returns. NOT annualized (trades are
        irregularly spaced in time, so annualizing would need extra
        assumptions this dataset doesn't support) — treat as a relative
        risk-adjusted-quality signal, not a textbook Sharpe/Sortino number.
        """
        if not pnl_pcts:
            return 0.0
        mean = float(np.mean(pnl_pcts))
        if downside_only:
            downside = [p for p in pnl_pcts if p < 0]
            std = float(np.std(downside)) if len(downside) > 1 else 0.0
        else:
            std = float(np.std(pnl_pcts)) if len(pnl_pcts) > 1 else 0.0
        return mean / std if std > 0 else 0.0

    @staticmethod
    def _streaks(pnls: list) -> tuple:
        """Max consecutive wins and max consecutive losses, in trade order."""
        max_win_streak = max_loss_streak = 0
        cur_win = cur_loss = 0
        for p in pnls:
            if p > 0:
                cur_win += 1
                cur_loss = 0
            else:
                cur_loss += 1
                cur_win = 0
            max_win_streak = max(max_win_streak, cur_win)
            max_loss_streak = max(max_loss_streak, cur_loss)
        return max_win_streak, max_loss_streak

    @staticmethod
    def _daily_pnl(trades: list) -> dict:
        daily = {}
        for t in trades:
            d = t.setup.entry_time.date()
            daily[d] = daily.get(d, 0.0) + t.result.net_pnl
        return daily

    @staticmethod
    def _exit_reason_breakdown(trades: list) -> dict:
        breakdown = {}
        for t in trades:
            reason = t.result.exit_reason
            entry = breakdown.setdefault(reason, {"count": 0, "total_pnl": 0.0})
            entry["count"] += 1
            entry["total_pnl"] += t.result.net_pnl
        for reason, entry in breakdown.items():
            entry["avg_pnl"] = entry["total_pnl"] / entry["count"]
        return breakdown

    @staticmethod
    def _kelly_fraction(win_rate: float, win_loss_ratio: float) -> float:
        """
        Classic Kelly criterion: f* = p - q/b, where p=win probability,
        q=1-p, b=win/loss ratio (odds). Clamped to [0, 1] — negative Kelly
        means the edge doesn't justify risking anything at these odds.
        """
        if win_loss_ratio <= 0 or not math.isfinite(win_loss_ratio):
            return 0.0
        p = win_rate
        q = 1 - win_rate
        f = p - q / win_loss_ratio
        return max(0.0, min(1.0, f))

    def _phase1_verdict(self, n: int, win_rate: float, ci_lower: float, ci_upper: float) -> tuple:
        if n < self.MIN_TRADES_FOR_VERDICT:
            return (
                "TOO FEW TRADES",
                f"{n} trades is below the {self.MIN_TRADES_FOR_VERDICT}-trade minimum for any "
                f"statistically meaningful conclusion. The 95% CI on win rate is "
                f"({ci_lower:.1%}, {ci_upper:.1%}) — wide enough that the true win rate could "
                f"plausibly be well above or below the {self.TARGET_WIN_RATE:.0%} target. "
                f"Need {self.TARGET_TRADE_COUNT - n} more trades minimum (per architecture v1.4) "
                f"before this number means anything."
            )
        if ci_upper < self.TARGET_WIN_RATE:
            return (
                "LIKELY BELOW TARGET",
                f"95% CI upper bound ({ci_upper:.1%}) is below the {self.TARGET_WIN_RATE:.0%} "
                f"target — even an optimistic read of this sample doesn't clear the bar."
            )
        if ci_lower > self.TARGET_WIN_RATE:
            return (
                "LIKELY ABOVE TARGET",
                f"95% CI lower bound ({ci_lower:.1%}) is above the {self.TARGET_WIN_RATE:.0%} "
                f"target — even a pessimistic read of this sample clears the bar."
            )
        return (
            "INCONCLUSIVE",
            f"{self.TARGET_WIN_RATE:.0%} target falls within the 95% CI "
            f"({ci_lower:.1%}, {ci_upper:.1%}) — more trades needed to resolve either way."
        )

    @staticmethod
    def _empty_report() -> PerformanceReport:
        return PerformanceReport(
            trade_count=0, win_rate=0.0, win_rate_ci_95=(0.0, 0.0),
            avg_win=0.0, avg_loss=0.0, win_loss_ratio=0.0, expectancy_per_trade=0.0,
            profit_factor=0.0, sharpe_like=0.0, sortino_like=0.0,
            max_consecutive_wins=0, max_consecutive_losses=0,
            daily_pnl={}, best_day=(None, 0.0), worst_day=(None, 0.0),
            pct_losing_days=0.0, exit_reason_pnl={}, kelly_fraction=0.0,
            phase1_verdict="NO TRADES", phase1_reasoning="No trades were executed.",
        )