# TODO / Development Backlog

> Priorities are based only on evidence from the repository and `docs.txt`. No new requirements have been invented.

## Critical

### C1 — Resolve W21 earnings-calendar source and fallback
**Evidence:** `docs.txt` calls W21 the highest operational priority and says it must be resolved before further code. The architecture requires skipping earnings days and treating unavailable calendar data as an earnings day.
**Relevant:** `docs.txt`, `core/data.py`, `core/features.py`.

### C2 — Establish strict v1.4 compliance gate before live capital
**Evidence:** Current implementation lacks several architecture rules. The documented Phase 1 gate requires live validation metrics before scaling.
**Relevant:** `docs.txt`, `docs/CURRENT_STATUS.md`.

### C3 — Verify/correct backtest entry-price semantics
**Evidence:** `core/signals.py` emits `signal_price` for next-bar-open execution, but `core/backtest.py` and `run_risk.py` size using `sig_row["close"]`.
**Relevant:** `core/signals.py`, `core/backtest.py`, `run_risk.py`.

## High

### H1 — Implement regime classification and hard skips
Add the architecture's Trending/Choppy/Wide-Gap classifier and session-level hard skips for regime/calendar conditions.
**Relevant:** `docs.txt`, `core/features.py`, missing `IntradayRegimeClassifier`.

### H2 — Implement short-side symmetry
Implement the architecture's mirrored short Phase A, invalidations, Phase B scoring, risk sizing and exits, if the target remains unchanged.
**Relevant:** `docs.txt`, `core/signals.py`, `core/risk.py`, `core/paper_trading.py`.

### H3 — Complete W22 missing-bar policy
Choose and implement the documented forward-fill or skip-session behavior; log all affected sessions and exclude unvalidated gap-affected sessions from performance statistics.
**Relevant:** `docs.txt`, `core/data.py`, `core/performance.py`.

### H4 — Add calendar/expiry fields and safe fallback
Populate `is_earnings_day`, `is_corporate_event_day`, weekly/monthly expiry tags and enforce documented actions.
**Relevant:** `docs.txt`, `core/data.py`, `core/features.py`.

### H5 — Complete required feature set and validity flags
Add/verify the architecture's momentum, sector-return/relative-strength, weekly levels, GIFT Nifty where available, `VWAP_reliable`, `Momentum_valid`, and related session context.
**Relevant:** `docs.txt`, `core/features.py`.

### H6 — Complete Phase-B and timeout semantics
Verify explicit B1/B2 behavior, entry cutoff/min-remaining-bars logic, exact bar-5 semantics, and abandoned-setup records with simulated fills.
**Relevant:** `docs.txt`, `core/signals.py`.

### H7 — Implement full performance diagnostics
Add regime/day/time/expiry/OR-vs-PDH/pullback/score/composition/entry-premium/S1+S3/bars-since-Phase-A/abandoned-setup diagnostics from Section 12.2.
**Relevant:** `docs.txt`, `core/performance.py`.

### H8 — Implement rolling live performance monitor
Track rolling-20 win rate and profit factor and implement the documented pause conditions.
**Relevant:** `docs.txt`, `core/performance.py`, `core/paper_trading.py`.

### H9 — Build walk-forward validation framework
Implement 2-year train + 6-month test, 5+ folds and 80+ completed out-of-sample trade accounting.
**Relevant:** `docs.txt`, `core/backtest.py`, `core/performance.py`.

### H10 — Add ±20% parameter stability tests
Verify that changing all configuration parameters by ±20% does not collapse results.
**Relevant:** `docs.txt`, `settings.yaml`.

### H11 — Measure paper-trading execution latency/slippage
Record Phase-B evaluation/order timing and separate live execution slippage from market-impact slippage as required by W23.
**Relevant:** `docs.txt`, `core/paper_trading.py`.

## Medium

### M1 — Resolve INFY/TCS sector-proxy confirmation
`run_paper_trading.py` currently restricts paper trading to three instruments and explicitly says INFY/TCS sector proxy handling is not yet confirmed.
**Relevant:** `run_paper_trading.py`, `core/data.py`.

### M2 — Remove duplicate/fragile configuration defaults
Reconcile `Settings` defaults with the YAML-driven architecture and ensure missing configuration does not silently create unintended strategy behavior.
**Relevant:** `config.py`, `settings.yaml`.

### M3 — Reconcile OR-length defaults
`SessionFeatureEngineer` has a constructor/docstring default of 6 while current YAML/callers use 3. Remove the ambiguity only after confirming the intended specification remains 3.
**Relevant:** `core/features.py`, `settings.yaml`.

### M4 — Review first-session ATR backfill
Verify whether `df["atr"].bfill()` is acceptable under the strict lookahead-free requirement; change only after the intended policy is confirmed.
**Relevant:** `core/features.py`.

### M5 — Review paper-state atomicity/concurrency
Assess whether JSON state needs locking or atomic writes for the cron execution model.
**Relevant:** `core/paper_trading.py`.

### M6 — Replace stale `core1` documentation references
Several module docstrings refer to `core1/*.py` even though the repository package is `core/`.
**Relevant:** `run_backtest.py`, `run_features.py`, `run_signals.py`, `run_risk.py`, `run_performance.py`, core module docstrings.

### M7 — Unify hardcoded instrument lists
Where the intended behavior is all configured instruments, replace script-specific subsets with `SETTINGS.instruments` after instrument readiness is confirmed.
**Relevant:** `run_paper_trading.py`, `run_performance.py`.

## Low

### L1 — Add formal automated tests
No test suite is present. Add unit/integration tests for data normalization, RVOL leakage, session features, signal state transitions, risk exits and paper-state persistence.
**Relevant:** repository-wide.

### L2 — Add CI/build configuration
No CI workflow or formal build configuration exists. Add one if project governance requires it.
**Relevant:** repository root.

### L3 — Add a repository README
There is no committed `README.md`. The new `docs/` package can serve as the detailed context layer; a concise user-facing README remains useful.
**Relevant:** repository root.

## Unknown priority

### U1 — Broker/data-source abstraction
The architecture names YFinance, Zerodha Kite, Angel One and Local CSV source abstractions, but current code is directly coupled to Angel One. Whether/when to implement the abstraction is not prioritized by code.
**Relevant:** `docs.txt`, `core/data.py`.

### U2 — Corporate-action and survivorship-bias controls
The architecture requires adjusted prices and historically representative instruments, but the repository does not establish an implementation plan or priority beyond the architecture text.
**Relevant:** `docs.txt`.

### U3 — Circuit-breaker handling
Architecture W15 requires flagging sessions where a stock hits its price band. Priority beyond W15 is not documented.
**Relevant:** `docs.txt`.

### U4 — Correlated sector exposure cap
W16 calls for max one position per sector at a time, while `core/backtest.py` explicitly allows simultaneous positions across instruments. The intended reconciliation is not documented.
**Relevant:** `docs.txt`, `core/backtest.py`.

## Do not treat as TODOs automatically

Known weaknesses W1–W20 are architecture research/validation concerns, not all immediate implementation tasks. Parameter heuristics, OR-window optimality, strategy decay, S1/S3 correlation and similar items should remain research/validation questions unless the project explicitly promotes them to implementation work.
