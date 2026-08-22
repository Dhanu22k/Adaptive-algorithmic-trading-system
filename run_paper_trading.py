"""
run_paper_trading.py — Step 8: cron entry point.

Set up via crontab (edit with `crontab -e`):

    */5 9-15 * * 1-5  cd /path/to/project && .venv/bin/python3 run_paper_trading.py >> paper_trading.log 2>&1

This runs every 5 minutes, Mon-Fri, 09:00-15:59 IST (the script itself
checks market hours precisely and skips outside 09:15-15:30 — the cron
schedule is intentionally a bit wider so it doesn't matter if the VM's
clock drifts slightly).

Output: appends to paper_trades.csv (completed trades) and
paper_trading.log (every run's activity, via the >> redirect above).
State persists in paper_state.json between runs.
"""

from pathlib import Path

from config import CREDENTIALS, SETTINGS
from core.data import AngelDataPipeline
from core.features import SessionFeatureEngineer
from core.signals import TwoPhaseSignalModel, SignalConfig
from core.risk import IntradayRiskManager, RiskConfig
from core.paper_trading import PaperTradingEngine

if __name__ == "__main__":
    pipeline     = AngelDataPipeline(**CREDENTIALS, cache_dir=SETTINGS.cache_dir)
    sfe          = SessionFeatureEngineer(or_end_bar=SETTINGS.or_end_bar)
    signal_model = TwoPhaseSignalModel(SignalConfig.from_settings(SETTINGS))
    risk_manager = IntradayRiskManager(RiskConfig.from_settings(SETTINGS))

    project_root = Path(__file__).parent.resolve()

    engine = PaperTradingEngine(
        pipeline=pipeline, sfe=sfe, signal_model=signal_model,
        risk_manager=risk_manager, history_days=SETTINGS.history_days,
        state_path=project_root / "paper_state.json",
        trades_csv_path=project_root / "paper_trades.csv",
    )

    # Same 3 confirmed-working instruments as run_backtest.py -- swap to
    # SETTINGS.instruments once INFY/TCS's sector token is fixed.
    tickers = ["HDFCBANK", "ICICIBANK", "RELIANCE"]

    print(f"{'='*55}")
    engine.run_once(tickers)
    print(f"{'='*55}")