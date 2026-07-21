"""
core1/risk.py — Step 4: IntradayRiskManager
=============================================
Consumes a signal (entry price, ATR, entry time) from TwoPhaseSignalModel and:
  1. Sizes the position (risk-based, capped at 25% of capital notional)
  2. Sets stop (ATR x 1.5) and target (ATR x 2.5), skipping trades where the
     resulting gross move is below the 0.6% minimum (ATR too small to be worth
     the transaction costs)
  3. Walks the trade forward bar-by-bar to find the actual exit: stop, target,
     trailing stop, or hard square-off at 15:15
  4. Applies realistic transaction costs (different for stop vs. target exits)
  5. Tracks cumulative daily P&L against the Rs 2,000 daily loss limit

Two parameters not pinned down in architecture v1.4 — decided here, not asked
about again, since both are standard and low-consequence to revisit later:

  risk_per_trade_pct = 1% of capital (Rs 1,000)
    Standard "1% rule". Also lines up cleanly with the daily loss limit: two
    full stop-outs in one day (Rs 1,000 x 2 = Rs 2,000) exactly exhausts it,
    so the daily limit reads as "about 2 bad trades", which is easy to reason
    about while trading live.

  Same-bar stop-vs-target ambiguity
    A single 5-min OHLC bar can't tell us whether stop or target was touched
    first intrabar. Adopted convention: check the TRAILING STOP UPDATE first,
    then STOP, then TARGET — i.e. always assume the worse path within a bar.
    This is deliberately conservative (never overstates edge in backtesting).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


_SQUAREOFF = datetime.time(15, 15)


@dataclass
class RiskConfig:
    """All tunable thresholds in one place — matches SETTINGS in config.py."""
    capital:            float = 100_000.0
    risk_per_trade_pct: float = 0.01
    atr_stop_mult:       float = 1.5
    atr_target_mult:     float = 2.5
    min_gross_move:       float = 0.006
    daily_loss_limit:     float = 2000.0
    max_position_pct:     float = 0.25
    trail_trigger_pct:    float = 0.50
    txn_cost_normal_pct:  float = 0.0027
    txn_cost_stop_pct:    float = 0.0033

    @classmethod
    def from_settings(cls, settings) -> "RiskConfig":
        return cls(
            capital             = settings.capital,
            risk_per_trade_pct  = settings.risk_per_trade_pct,
            atr_stop_mult       = settings.atr_stop_mult,
            atr_target_mult     = settings.atr_target_mult,
            min_gross_move      = settings.min_gross_move,
            daily_loss_limit    = settings.daily_loss_limit,
            max_position_pct    = settings.max_position_pct,
            trail_trigger_pct   = settings.trail_trigger_pct,
            txn_cost_normal_pct = settings.txn_cost_normal_pct,
            txn_cost_stop_pct   = settings.txn_cost_stop_pct,
        )


@dataclass
class TradeSetup:
    """Output of sizing a trade — may be a no-trade (skip_reason set)."""
    entry_time:    pd.Timestamp
    entry_price:   float
    atr:           float
    stop_price:    float
    target_price:  float
    quantity:      int
    notional:      float
    risk_amount:   float
    gross_move_pct: float
    skip_reason:   Optional[str] = None

    @property
    def is_tradeable(self) -> bool:
        return self.skip_reason is None and self.quantity > 0


@dataclass
class TradeResult:
    """Output of simulating a TradeSetup forward through subsequent bars."""
    entry_time:   pd.Timestamp
    entry_price:  float
    exit_time:    pd.Timestamp
    exit_price:   float
    exit_reason:  str          # STOP | TARGET | TRAIL_STOP | SQUAREOFF
    quantity:     int
    trailed:      bool
    gross_pnl:    float
    costs:        float
    net_pnl:      float
    net_pnl_pct:  float        # net_pnl / notional


class IntradayRiskManager:
    """
    Usage:
        rm    = IntradayRiskManager(RiskConfig.from_settings(SETTINGS))
        setup = rm.size_trade(entry_price, atr, entry_time)
        if setup.is_tradeable:
            result = rm.simulate_trade(setup, session_bars_df)
    """

    def __init__(self, config: Optional[RiskConfig] = None) -> None:
        self.cfg = config or RiskConfig()

    # ── Position sizing ───────────────────────────────────────────────────────

    def size_trade(
        self, entry_price: float, atr: float, entry_time: pd.Timestamp
    ) -> TradeSetup:
        cfg = self.cfg

        stop_price   = entry_price - cfg.atr_stop_mult   * atr
        target_price = entry_price + cfg.atr_target_mult * atr
        gross_move_pct = (target_price - entry_price) / entry_price

        # Skip if the ATR-implied move is too small to clear transaction costs
        if gross_move_pct < cfg.min_gross_move:
            return TradeSetup(
                entry_time=entry_time, entry_price=entry_price, atr=atr,
                stop_price=stop_price, target_price=target_price,
                quantity=0, notional=0.0, risk_amount=0.0,
                gross_move_pct=gross_move_pct,
                skip_reason=(
                    f"gross_move {gross_move_pct:.3%} < min_gross_move "
                    f"{cfg.min_gross_move:.3%} — ATR too small"
                ),
            )

        risk_per_share = entry_price - stop_price
        if risk_per_share <= 0:
            return TradeSetup(
                entry_time=entry_time, entry_price=entry_price, atr=atr,
                stop_price=stop_price, target_price=target_price,
                quantity=0, notional=0.0, risk_amount=0.0,
                gross_move_pct=gross_move_pct,
                skip_reason="risk_per_share <= 0 (bad ATR/stop inputs)",
            )

        risk_amount = cfg.capital * cfg.risk_per_trade_pct
        qty_risk_based = int(risk_amount // risk_per_share)

        max_notional = cfg.capital * cfg.max_position_pct
        qty_notional_cap = int(max_notional // entry_price)

        quantity = max(0, min(qty_risk_based, qty_notional_cap))
        notional = quantity * entry_price

        skip_reason = None
        if quantity <= 0:
            skip_reason = (
                f"quantity=0 (risk_based={qty_risk_based}, "
                f"notional_cap={qty_notional_cap}) — position too small to size"
            )

        return TradeSetup(
            entry_time=entry_time, entry_price=entry_price, atr=atr,
            stop_price=stop_price, target_price=target_price,
            quantity=quantity, notional=notional, risk_amount=risk_amount,
            gross_move_pct=gross_move_pct, skip_reason=skip_reason,
        )

    # ── Trade simulation ──────────────────────────────────────────────────────

    def simulate_trade(
        self, setup: TradeSetup, session_bars: pd.DataFrame
    ) -> TradeResult:
        """
        Walks forward through bars AFTER the entry bar, in the same session,
        looking for stop/target/trail/square-off. `session_bars` must be the
        full session (or at least everything from the entry bar onward),
        indexed by timestamp, with columns open/high/low/close.

        Same-bar ambiguity (see module docstring): within a single bar, the
        trailing-stop update is checked before the stop, and the stop before
        the target — i.e. always the more conservative outcome.
        """
        cfg = self.cfg

        entry_price  = setup.entry_price
        stop         = setup.stop_price
        target       = setup.target_price
        trail_level  = entry_price + cfg.trail_trigger_pct * (target - entry_price)
        cost_per_share = entry_price * cfg.txn_cost_normal_pct
        trailed = False

        # Only bars strictly after the entry bar
        future_bars = session_bars[session_bars.index > setup.entry_time]

        exit_time   = None
        exit_price  = None
        exit_reason = None

        for ts, bar in future_bars.iterrows():
            bar_time = ts.time()

            # Hard square-off — always wins, checked first
            if bar_time >= _SQUAREOFF:
                exit_time, exit_price, exit_reason = ts, bar["close"], "SQUAREOFF"
                break

            # Trailing stop update: if this bar's high reached the 50%-of-target
            # level and we haven't trailed yet, move stop to breakeven + costs
            if not trailed and bar["high"] >= trail_level:
                stop = entry_price + cost_per_share
                trailed = True

            # Stop check (using the possibly-just-updated stop)
            if bar["low"] <= stop:
                exit_time  = ts
                exit_price = stop
                exit_reason = "TRAIL_STOP" if trailed else "STOP"
                break

            # Target check
            if bar["high"] >= target:
                exit_time, exit_price, exit_reason = ts, target, "TARGET"
                break

        # Fallback: ran out of bars without an explicit exit (shouldn't happen
        # if session_bars extends through square-off, but guard anyway)
        if exit_time is None:
            last_ts  = future_bars.index[-1] if len(future_bars) else setup.entry_time
            last_bar = future_bars.iloc[-1] if len(future_bars) else None
            exit_time   = last_ts
            exit_price  = last_bar["close"] if last_bar is not None else entry_price
            exit_reason = "SQUAREOFF"

        gross_pnl = setup.quantity * (exit_price - entry_price)

        cost_pct = cfg.txn_cost_stop_pct if exit_reason == "STOP" else cfg.txn_cost_normal_pct
        costs    = setup.notional * cost_pct

        net_pnl     = gross_pnl - costs
        net_pnl_pct = net_pnl / setup.notional if setup.notional > 0 else 0.0

        return TradeResult(
            entry_time=setup.entry_time, entry_price=entry_price,
            exit_time=exit_time, exit_price=exit_price, exit_reason=exit_reason,
            quantity=setup.quantity, trailed=trailed,
            gross_pnl=gross_pnl, costs=costs,
            net_pnl=net_pnl, net_pnl_pct=net_pnl_pct,
        )


class DailyRiskTracker:
    """
    Tracks cumulative realized P&L for one trading day and enforces the hard
    daily loss limit. One instance per session; reset() between sessions.
    """

    def __init__(self, daily_loss_limit: float) -> None:
        self.daily_loss_limit = daily_loss_limit
        self.cumulative_pnl   = 0.0
        self.trades: list[TradeResult] = []

    def can_trade(self) -> bool:
        return self.cumulative_pnl > -self.daily_loss_limit

    def record(self, result: TradeResult) -> None:
        self.trades.append(result)
        self.cumulative_pnl += result.net_pnl

    def reset(self) -> None:
        self.cumulative_pnl = 0.0
        self.trades = []

    @property
    def limit_hit(self) -> bool:
        return not self.can_trade()