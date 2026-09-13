# AGENTS.md — Adaptive Algorithmic Trading System

## Project Context

**Before making substantial changes, read `docs/PROJECT_CONTEXT.md`, `docs/ARCHITECTURE.md`, `docs/REQUIREMENTS.md`, `docs/DECISIONS.md`, and `docs/CURRENT_STATUS.md`.**

- [Project context](docs/PROJECT_CONTEXT.md)
- [Actual architecture](docs/ARCHITECTURE.md)
- [Requirements](docs/REQUIREMENTS.md)
- [Decisions](docs/DECISIONS.md)
- [Current status](docs/CURRENT_STATUS.md)
- [Backlog](docs/TODO.md)
- [v1.4 architecture/strategy specification](docs.txt)

## What this project is

A Python quantitative-trading system implementing an intraday NSE cash-equity ORB strategy. The current implementation is centered on Angel One SmartAPI, 5-minute OHLCV, session feature engineering, a two-phase pullback signal model, ATR-based risk management, multi-instrument backtesting, performance analysis and cron-driven paper trading.

## Rules for AI coding agents

1. Treat `docs.txt` as the **v1.4 target architecture/requirements document**, not proof that every requirement exists in code.
2. Treat `core/*.py` and `settings.yaml` as the primary implementation truth.
3. Do not assume a frontend, backend HTTP API, database, migrations, Docker, CI or infrastructure layer exists; none is present in the repository tree.
4. Preserve the existing root orchestration pattern: `run_*.py` scripts construct core components and primarily orchestrate/print results.
5. Preserve the `from config import CREDENTIALS, SETTINGS` interface unless a deliberate architecture change is explicitly approved.
6. Keep strategy parameters centralized in `settings.yaml`; avoid introducing new unexplained hardcoded trading thresholds.
7. Maintain the strict bar-close/IST convention and the RVOL no-future-leakage rule.
8. Do not silently change strategy semantics while fixing plumbing/data issues. Separate architecture/spec deviations from bug fixes.
9. Be especially careful with `core/data.py`: it owns authentication, external API calls, timestamp normalization, caching, sector-proxy resolution and RVOL preparation.
10. Be especially careful with `core/signals.py` and `core/risk.py`: changes can alter backtest results and paper/live behavior.
11. Preserve the shared `core/risk.py::_check_bar_exit()` path between backtest and paper trading unless there is an explicit reason to change that coupling.
12. Never expose, print, commit or place real credentials/tokens/MPIN/TOTP secrets in documentation, tests or source. `.env` is ignored and credentials belong there.
13. Do not claim live trading readiness from backtest output alone. The architecture requires Phase 0 paper validation and 80+ live Phase-1 trades before scaling.
14. When implementation and `docs.txt` disagree, document the discrepancy first and verify the intended rule before changing behavior.
15. Prefer surgical, reviewable changes over broad rewrites.

## Important architecture constraints

- Five-minute primary timeframe.
- Bar-close timestamps in Asia/Kolkata.
- ₹1,00,000 validation capital.
- 1% risk per trade; ₹2,000 daily loss limit.
- 25% maximum position notional.
- 0.6% minimum gross target.
- 15:15 IST hard square-off.
- No overnight positions.
- Pullback entry is intentional; do not convert to raw breakout without an explicit strategy decision.
- First OR break determines session direction.
- The v1.4 target includes long and short logic, but current code is long-only.

## Coding conventions discovered

- Python type annotations are common.
- Dataclasses are used for configuration and state/result objects.
- Pandas DataFrames are the principal internal data structure.
- Core classes have explicit public methods and detailed module docstrings.
- Root scripts are thin orchestration layers.
- Console logging is used extensively for validation and diagnostics.
- File persistence uses Parquet/JSON/CSV rather than a database.

## Build/run commands

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

The repository requires a `.env` containing the four Angel credentials expected by `config.py`. Do not commit it.

Manual validation entry points:

```bash
python run_data.py
python run_features.py
python run_signals.py
python run_risk.py
python run_backtest.py
python run_performance.py
python run_paper_trading.py
```

`run_paper_trading.py` is designed for repeated OS cron execution every five minutes on weekdays; the script itself enforces 09:15–15:30 market hours.

No formal automated test command exists because no test framework/configuration is committed. `check_import_paths.py` and `debug_reproducibility.py` are diagnostic scripts.

## Testing requirements for changes

Before merging strategy/data changes:

- run the most relevant root validation script(s);
- verify timestamp and session behavior;
- verify no RVOL lookahead is introduced;
- verify signal counts and trade results before/after when changing signal/risk logic;
- test paper-state behavior if touching `core/paper_trading.py`;
- never use real credentials in tests or output.

For future strategy changes, the v1.4 requirements call for walk-forward validation, 80+ out-of-sample trades and ±20% parameter stability before treating results as validated.

## Sensitive files/directories

Treat these carefully:

- `.env` / credential files — secrets.
- `settings.yaml` — strategy behavior and risk parameters.
- `core/data.py` — external market-data correctness.
- `core/features.py` — feature/lookahead correctness.
- `core/signals.py` — entry-state semantics.
- `core/risk.py` — capital/risk/exit semantics.
- `core/backtest.py` — validation methodology.
- `core/paper_trading.py` — persistent trading state.
- `paper_state.json` / `paper_trades.csv` — runtime trading records when present locally.
- `data_cache/` — generated market-data cache; ignored by Git.

## Documentation maintenance

When architecture, requirements, strategy semantics, external integrations, persistence, deployment or validation methodology changes:

1. update the relevant `docs/*.md` file(s);
2. update `docs/CURRENT_STATUS.md` when implementation state changes;
3. update `docs/TODO.md` when backlog priority/status changes;
4. update `docs/DECISIONS.md` for a durable design/business decision;
5. update `docs/ARCHITECTURE.md` when component relationships or data flow change;
6. keep `docs.txt` as the historical/v1.4 architecture source unless the project explicitly replaces it.

## Unknowns

Use the exact phrase **`UNKNOWN — not established from the repository.`** when a fact cannot be determined. Do not fill repository gaps with assumptions.

## Final agent checklist

Before completing a substantial change, confirm:

- the change matches the intended architecture/requirements;
- no secret was added;
- no unintended strategy rule changed;
- relevant validation scripts/tests were run;
- documentation reflects the new state;
- backtest/live semantic differences are understood and explicitly documented.
