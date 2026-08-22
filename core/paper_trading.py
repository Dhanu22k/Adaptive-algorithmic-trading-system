"""
core/paper_trading.py — Step 8: Paper Trading Engine
=======================================================
Designed to be invoked by cron every 5 minutes during market hours
(09:15-15:30 IST). Each invocation is a fresh, short-lived process:

  1. Loads persisted state from paper_state.json (open positions, and
     which signals have already been logged today, per instrument)
  2. Fetches each instrument's cached history + TODAY's bars-so-far
  3. Checks any OPEN positions against the latest bar (using the exact
     same _check_bar_exit logic the backtester uses — see core/risk.py)
  4. Checks for a NEW signal on today's session that hasn't been acted on
  5. Persists updated state, appends any closed/opened trades to
     paper_trades.csv
  6. Exits — no long-running process, nothing to crash or leak memory

Why cron + JSON state instead of a long-running loop: see conversation —
cron gets free crash-recovery (a failed run just retries in 5 min) without
needing a supervisor process, and this strategy only cares about 5-min bar
closes anyway, so cron's natural granularity is a fit, not a limitation.
"""

from __future__ import annotations

import csv
import datetime
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import pandas as pd

from core.data import AngelDataPipeline, InstrumentConfig
from core.features import SessionFeatureEngineer
from core.signals import TwoPhaseSignalModel, SignalConfig
from core.risk import IntradayRiskManager, RiskConfig, TradeSetup


_MARKET_OPEN  = datetime.time(9, 15)
_MARKET_CLOSE = datetime.time(15, 30)


@dataclass
class OpenPosition:
    """One live paper position, persisted across cron runs until it exits."""
    ticker:          str
    entry_time:      str     # ISO string — JSON can't store pd.Timestamp directly
    entry_price:     float
    stop:            float
    target:          float
    trail_level:     float
    cost_per_share:  float
    trailed:         bool
    quantity:        int
    notional:        float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "OpenPosition":
        return cls(**d)


class PaperTradingState:
    """
    Persisted to paper_state.json between cron runs.

    Tracks, per instrument:
      - open_position: OpenPosition or None
      - last_signal_date: the last session_date we already checked/acted on
        for a NEW signal (prevents re-logging the same signal on every
        5-min cron run within the same day)
    """

    def __init__(self, path: Path):
        self.path = path
        self.open_positions: dict[str, Optional[OpenPosition]] = {}
        self.last_checked_date: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with open(self.path, "r") as f:
            raw = json.load(f)
        for ticker, pos_dict in raw.get("open_positions", {}).items():
            self.open_positions[ticker] = (
                OpenPosition.from_dict(pos_dict) if pos_dict else None
            )
        self.last_checked_date = raw.get("last_checked_date", {})

    def save(self) -> None:
        raw = {
            "open_positions": {
                t: (p.to_dict() if p else None) for t, p in self.open_positions.items()
            },
            "last_checked_date": self.last_checked_date,
        }
        with open(self.path, "w") as f:
            json.dump(raw, f, indent=2)


class PaperTradingEngine:
    """
    Usage (called once per cron invocation, via run_paper_trading.py):
        engine = PaperTradingEngine(pipeline, sfe, signal_model, risk_manager, settings)
        engine.run_once(["HDFCBANK", "ICICIBANK", "RELIANCE"])
    """

    def __init__(
        self, pipeline: AngelDataPipeline, sfe: SessionFeatureEngineer,
        signal_model: TwoPhaseSignalModel, risk_manager: IntradayRiskManager,
        history_days: int, state_path: Path, trades_csv_path: Path,
    ) -> None:
        self.pipeline     = pipeline
        self.sfe          = sfe
        self.signal_model = signal_model
        self.risk_manager = risk_manager
        self.history_days = history_days
        self.state         = PaperTradingState(state_path)
        self.trades_csv    = trades_csv_path
        self._ensure_csv_header()

    def _ensure_csv_header(self) -> None:
        if not self.trades_csv.exists():
            with open(self.trades_csv, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "ticker", "entry_time", "entry_price", "exit_time",
                    "exit_price", "exit_reason", "quantity", "trailed",
                    "gross_pnl", "costs", "net_pnl", "net_pnl_pct",
                ])

    def _log_closed_trade(self, ticker: str, exit_time, exit_price: str,
                           exit_reason: str, pos: OpenPosition) -> None:
        gross_pnl = pos.quantity * (exit_price - pos.entry_price)
        cost_pct  = (self.risk_manager.cfg.txn_cost_stop_pct
                     if exit_reason in ("STOP", "TRAIL_STOP")
                     else self.risk_manager.cfg.txn_cost_normal_pct)
        costs     = pos.notional * cost_pct
        net_pnl   = gross_pnl - costs
        net_pct   = net_pnl / pos.notional if pos.notional > 0 else 0.0

        with open(self.trades_csv, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                ticker, pos.entry_time, pos.entry_price, exit_time,
                exit_price, exit_reason, pos.quantity, pos.trailed,
                round(gross_pnl, 2), round(costs, 2), round(net_pnl, 2),
                round(net_pct, 4),
            ])
        print(f"[Paper] CLOSED {ticker}: {exit_reason} @ {exit_price:.2f}  "
              f"net_pnl=₹{net_pnl:.2f}")

    def run_once(self, tickers: list) -> None:
        now = datetime.datetime.now(tz=datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
        if not (_MARKET_OPEN <= now.time() <= _MARKET_CLOSE):
            print(f"[Paper] {now.time()} outside market hours ({_MARKET_OPEN}-{_MARKET_CLOSE}) — skipping run.")
            return

        for ticker in tickers:
            try:
                self._process_instrument(ticker)
            except Exception as exc:
                print(f"[Paper] ⚠ {ticker} failed this run: {exc}")
                print(f"[Paper]   Will retry next cron cycle — state unaffected.")
                continue

        self.state.save()

    def _process_instrument(self, ticker: str) -> None:
        config = InstrumentConfig.from_ticker(ticker)

        # use_cache=True: history portion reuses the existing 20h-TTL cache.
        # Today's still-forming session is always included in whatever the
        # underlying fetch returns as the most recent data available.
        stock_df, sector_df = self.pipeline.fetch_with_sector(
            config, days=self.history_days
        )
        features_df = self.sfe.compute(stock_df, sector_df, config)
        signals_df  = self.signal_model.generate(features_df)

        today = datetime.date.today()
        today_df = signals_df[signals_df["session_date"] == today]
        if today_df.empty:
            print(f"[Paper] {ticker}: no bars for today yet.")
            return

        latest_bar = today_df.iloc[-1]
        latest_ts  = today_df.index[-1]

        # ── Check open position against the latest bar first ──────────────
        pos = self.state.open_positions.get(ticker)
        if pos is not None:
            bar_time = latest_ts.time()
            trail_level = pos.entry_price + self.risk_manager.cfg.trail_trigger_pct * (
                pos.target - pos.entry_price
            )
            exit_price, exit_reason, new_stop, new_trailed = self.risk_manager._check_bar_exit(
                pos.entry_price, pos.stop, pos.target, trail_level,
                pos.cost_per_share, pos.trailed, latest_bar, bar_time,
            )
            if exit_reason is not None:
                self._log_closed_trade(ticker, latest_ts.isoformat(), exit_price, exit_reason, pos)
                self.state.open_positions[ticker] = None
            else:
                pos.stop    = new_stop
                pos.trailed = new_trailed
                self.state.open_positions[ticker] = pos
                print(f"[Paper] {ticker}: position open, stop={pos.stop:.2f}, trailed={pos.trailed}")
            return  # one position at a time per instrument — don't also check for new signals

        # ── No open position — check for a NEW signal not yet acted on ────
        already_checked = self.state.last_checked_date.get(ticker) == str(today)
        if latest_bar.get("signal") == "LONG" and not already_checked:
            setup = self.risk_manager.size_trade(
                entry_price=latest_bar["signal_price"], atr=latest_bar["atr"],
                entry_time=latest_ts,
            )
            if setup.is_tradeable:
                trail_level    = setup.entry_price + self.risk_manager.cfg.trail_trigger_pct * (
                    setup.target_price - setup.entry_price
                )
                cost_per_share = setup.entry_price * self.risk_manager.cfg.txn_cost_normal_pct
                self.state.open_positions[ticker] = OpenPosition(
                    ticker=ticker, entry_time=latest_ts.isoformat(),
                    entry_price=setup.entry_price, stop=setup.stop_price,
                    target=setup.target_price, trail_level=trail_level,
                    cost_per_share=cost_per_share, trailed=False,
                    quantity=setup.quantity, notional=setup.notional,
                )
                print(f"[Paper] OPENED {ticker}: entry={setup.entry_price:.2f} "
                      f"stop={setup.stop_price:.2f} target={setup.target_price:.2f} "
                      f"qty={setup.quantity}")
            else:
                print(f"[Paper] {ticker}: signal fired but not tradeable ({setup.skip_reason})")
            self.state.last_checked_date[ticker] = str(today)
        else:
            print(f"[Paper] {ticker}: no new signal.")