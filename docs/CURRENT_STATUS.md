# Current Status

> Evidence-based snapshot of the committed repository. This is not a claim that the full v1.4 architecture is complete.

## Repository state

- Default branch: `master`.
- Context baseline inspected: commit `9e796a605e35691f44339a385284f72f4a2105a4`.
- The repository contains implementation for data, features, signals, risk, backtesting, performance and paper trading.
- No committed `README.md` exists.
- No committed automated test suite was found.
- No deployment manifest or CI workflow was found.

## Completed/implemented functionality

### Data pipeline

`core/data.py` implements Angel One authentication, five-minute historical fetching, timestamp normalization, missing-bar detection, prior-session RVOL, data validation, Parquet caching, sector FUTIDX proxy resolution, and a fresh-today merge path.

The data layer also contains a 30-calendar-day RVOL warmup buffer and a wall-clock-based trim in `_fetch_compute_trim()`.

### Session features

`SessionFeatureEngineer` computes session metadata, 15-minute OR using three configured bars, cumulative VWAP, daily previous-session ATR, gap regime, previous-day high, price-position features and sector VWAP/consecutive-below-VWAP state.

### Signal model

`TwoPhaseSignalModel` implements long-side Phase A/Phase B processing, breakout buffer, RVOL/close-position requirements, invalidation state, highest-high tracking, pullback scoring and next-bar-open signal pricing.

### Risk/backtest

`IntradayRiskManager` implements long-side sizing, notional/risk caps, ATR stop/target, trailing stop and square-off. `DailyRiskTracker` applies a daily realized-P&L limit. `IntradayBacktestEngine` supports multiple instruments and shared daily risk.

### Performance

`PerformanceAnalyzer` implements basic statistical performance analysis and a Phase-1 verdict.

### Configuration

`settings.yaml` exists and `config.py` loads it into the existing `SETTINGS` interface. `.env` supplies broker credentials.

### Paper trading

`PaperTradingEngine` supports cron-driven short-lived cycles with persisted JSON state and CSV trade logging. `run_paper_trading.py` is the entry point.

## Partially implemented / not yet v1.4-complete

1. **Short side:** architecture specifies a mirrored short strategy; current signal/risk/paper flow is long-only.
2. **Regime classifier:** v1.4 specifies Trending/Choppy/Wide-Gap classification and hard skips; no dedicated `IntradayRegimeClassifier` exists.
3. **Calendar/event layer:** earnings/corporate-event flags, expiry tags and safe missing-calendar fallback are not implemented.
4. **W21:** the architecture explicitly requires a reliable earnings-calendar source before trading; the repository does not identify or implement one.
5. **W22:** gap detection exists, but the required forward-fill/skip-session policy and exclusion from performance are not fully implemented.
6. **Feature completeness:** several v1.4 features/validity flags are absent, including momentum, weekly levels, sector returns/relative strength and explicit `VWAP_reliable`/`Momentum_valid` fields.
7. **Phase-B rules:** the implementation has the low-touch pullback gate, but does not explicitly represent every v1.4 B2/scoring detail as an independent rule/diagnostic.
8. **Abandoned setup diagnostics:** v1.4 requires timeout logging with score/fill information; this is not implemented in the signal output.
9. **Performance diagnostics:** current analyzer is substantially smaller than the full Section 12.2 diagnostic set.
10. **Live execution:** paper trading simulates state and exits but does not place real broker orders or measure actual order latency/slippage.
11. **Rolling monitor:** the v1.4 rolling-20 pause condition is not implemented.
12. **Walk-forward validation:** no dedicated 2-year/6-month, 5+ fold validation framework is present.
13. **Parameter stability:** no ±20% parameter-stability test harness is present.
14. **Instrument coverage in paper runner:** `run_paper_trading.py` currently uses only HDFCBANK, ICICIBANK and RELIANCE.

## Known code-level concerns

- `core/backtest.py` and `run_risk.py` size trades from `sig_row["close"]`, whereas `core/signals.py` carries the intended `signal_price` for the next-bar-open fill. This creates a potential backtest/live entry-price mismatch and must be verified before trusting backtest results.
- `core/features.py` documents a default OR length of 6 in its class docstring/constructor default, while callers pass `SETTINGS.or_end_bar` and YAML sets it to 3. The caller path therefore uses 3, but the duplicate default is fragile.
- `MissingBarDetector` treats only sessions with >10% missing bars as significant, whereas v1.4 requires an explicit policy for session mismatches.
- `SessionFeatureEngineer._add_atr()` backfills the first session's missing previous-day ATR, which is a behavior that should be reviewed against strict lookahead-free requirements.
- `Settings` has built-in defaults even though the architecture says tunable parameters should be configuration-driven; missing YAML keys silently fall back to those defaults.
- Sector FUTIDX proxy selection is dependent on current Angel ScripMaster metadata.
- Paper state is a plain JSON file with no locking/transaction mechanism.

## Test/build status

No automated test framework or test directory was found in the repository. The root `run_*.py` files are manual validation/orchestration scripts rather than a formal test suite.

The repository is syntactically ordinary Python, but no repository CI/build result is available. **Build/CI status: UNKNOWN — not established from repository configuration.**

## Technical debt / warnings

- `docs.txt` describes a target architecture that is materially ahead of the implementation.
- Several root scripts still contain hardcoded instrument subsets or historical test windows.
- Comments/docstrings still contain historical `core1/...` paths even though the actual package is `core/`.
- The signal module reports `model_version=1.3` while comments reference v1.4 fixes.
- The architecture's broker-agnostic abstraction is not represented as an actual abstract data-source interface in code.

## Validation maturity

The repository should currently be considered **development / Phase 0 preparation and paper-trading implementation**, not production-live validated. The v1.4 requirement for 80+ completed out-of-sample trades and live validation metrics has not been demonstrated by repository artifacts.

## Important distinction

A feature being described in `docs.txt` does not mean it exists in code. Future agents should use `core/*.py` and `settings.yaml` as the implementation truth, and use `docs.txt` as the target architecture/requirements source.
