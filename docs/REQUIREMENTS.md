# Requirements

> Requirements are extracted from committed code and `docs.txt`. **Confirmed** means directly represented in code/config or explicitly stated in the architecture. **Inferred** means a reasonable interpretation that is not itself an explicit requirement.

## Functional requirements

### Confirmed by implementation

1. Load Angel One credentials from environment variables rather than hardcoding them (`config.py`).
2. Load tunable strategy settings from `settings.yaml` into `SETTINGS` (`config.py`).
3. Support the five configured instruments: HDFCBANK, ICICIBANK, RELIANCE, INFY and TCS (`settings.yaml`).
4. Authenticate to Angel One using client code, MPIN and TOTP (`core/data.py`).
5. Retrieve five-minute historical candles from Angel One.
6. Normalize retrieved timestamps to IST bar-close timestamps.
7. Cache historical market data in Parquet.
8. Resolve sector FUTIDX proxy contracts from Angel One ScripMaster when required.
9. Detect significant session-level missing-bar conditions and tag affected rows (`core/data.py`).
10. Calculate RVOL_ToD using prior sessions only.
11. Generate session features including OR, VWAP, ATR, gap, previous-day high and sector-VWAP features.
12. Detect Phase-A long breakout setups and evaluate Phase-B pullback candidates.
13. Simulate long trade exits through stop, target, trailing stop and square-off.
14. Apply risk-based position sizing and a 25% notional cap.
15. Apply a ₹2,000 daily loss limit through `DailyRiskTracker`.
16. Run multi-instrument backtests with a shared daily risk tracker.
17. Produce performance statistics including Wilson confidence intervals and a Phase-1 verdict.
18. Run short-lived paper-trading cycles and persist state in JSON.
19. Log completed paper trades to CSV.
20. Reuse `_check_bar_exit()` between backtest simulation and paper-trading position management.

### Confirmed by v1.4 architecture but not fully implemented

1. Support both long and short entry logic.
2. Enforce calendar-based earnings/corporate-event hard skips with a safe fallback when the calendar is unavailable.
3. Classify Trending/Choppy/Wide-Gap regimes and skip Regime 1/2 sessions.
4. Tag weekly/monthly expiry sessions.
5. Apply half-size on weekly expiry and skip monthly expiry.
6. Implement the complete v1.4 feature set, including validity flags, momentum, sector returns/relative strength, GIFT Nifty where available, weekly levels and diagnostic features.
7. Implement a complete W22 missing-bar policy: forward-fill or skip-session, log gaps, and exclude affected sessions from performance unless the fill method is validated.
8. Implement full event-driven session logic including all hard skips and session state transitions.
9. Record abandoned Phase-A setups with simulated fills.
10. Implement the full performance diagnostic breakdown from Section 12.2.
11. Measure and log live broker/API latency and execution slippage during paper trading.
12. Provide rolling performance monitoring and pause conditions.
13. Perform walk-forward validation with 2 years of training, 6 months of test and 5+ folds.
14. Perform ±20% parameter stability tests.
15. Progress to live Phase 1 only after Phase 0 paper validation.

## Non-functional requirements

### Confirmed

- No future leakage in RVOL_ToD: denominator must use prior sessions only.
- Timestamp normalization to bar-close/IST is mandatory in the architecture.
- Data should be validated before downstream processing.
- Historical fetches should reject partial history rather than silently cache it.
- The live paper path must not depend on a stale historical-cache snapshot for today's bars.
- The strategy is intended to be intraday-only with hard square-off at 15:15 IST.
- Configuration should be centralized for controlled parameter testing.

### Architecture targets

- 80+ completed out-of-sample trades for statistical validation.
- >58% win rate.
- >1.5 profit factor.
- >₹50 expectancy per trade after costs.
- >1.3:1 net average win/loss.
- Parameter stability under ±20% changes.

## Business rules

### Confirmed by v1.4 architecture

- Validation capital is ₹1,00,000.
- Maximum risk per trade is 1% of capital (₹1,000).
- Maximum daily loss is 2% (₹2,000).
- Maximum position notional is 25% of capital (₹25,000).
- Minimum gross target is 0.6%.
- No overnight holdings.
- Square-off at 15:15 IST.
- Entry window is after 09:30 and before 14:30, with at least four bars remaining when Phase A fires.
- First OR boundary break sets session direction; no opposing trades.
- No re-entry after stop-out or invalidation.
- Weekly expiry is half-size; monthly expiry is skipped.
- Earnings/corporate-event days are skipped.
- If the earnings calendar is unavailable, the safe fallback is to treat the day as an earnings day and skip.

## Technical constraints

- Five-minute primary timeframe.
- Angel One is the implemented broker/data API.
- Market-data storage is local Parquet.
- Paper state is local JSON.
- Trade log is local CSV.
- Runtime credentials are environment variables.
- Repository has no database layer.
- Repository has no web API or frontend.
- Exact Python version is UNKNOWN — not pinned.

## Security requirements

- Credentials must not be hardcoded.
- `.env` and credential files are Git-ignored.
- Actual API keys, MPINs, TOTP secrets, JWTs or refresh tokens must never be written to documentation or committed files.
- Broker authentication is the only demonstrated authorization boundary; no local application authorization model exists.

## Data requirements

- OHLCV: open, high, low, close, volume.
- Five-minute timestamps must use bar-close convention in IST.
- Duplicate timestamps must not remain in normalized cached data.
- Missing bars must be detected per session.
- RVOL_ToD must be based only on prior sessions.
- Corporate-action adjustment and survivorship-bias controls are required by architecture but are not established as implemented in the current code.
- Historical expiry/calendar tags are required by architecture but not established as implemented.

## Confirmed vs inferred notes

- **Confirmed:** `settings.yaml` has `history_days: 180`, while several validation scripts still explicitly request 30 days or hardcode a three-instrument subset.
- **Confirmed:** current `TwoPhaseSignalModel` produces `LONG` only.
- **Confirmed:** current missing-bar logic uses a >10% threshold rather than an explicit fill/skip policy.
- **Inferred:** the intended production deployment is a cron-managed VM process because the paper runner contains explicit cron instructions. Exact production host and infrastructure are not repository facts.
