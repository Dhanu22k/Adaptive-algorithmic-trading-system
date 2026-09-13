# Decisions

This file records decisions evidenced by `docs.txt` and the implementation. It does not invent rationale; unknown rationale is marked accordingly.

## D1 — No re-entry after stop-out or invalidation
**Decision:** No re-entry after a stop-out or invalidation.
**Rationale:** The architecture says this prevents overtrading.
**Alternatives:** UNKNOWN — not documented.
**Consequences:** A failed setup prevents later re-entry in that session.
**Files:** `docs.txt`, `core/signals.py`.

## D2 — First-break direction / one trade per session
**Decision:** The first OR boundary break establishes session direction; opposing trades are not allowed.
**Rationale:** Explicit architecture decision.
**Alternatives:** UNKNOWN — not documented.
**Consequences:** Current signal state also limits the session to one fired signal.
**Files:** `docs.txt`, `core/signals.py`.

## D3 — Equity at ₹1L validation capital
**Decision:** Use NSE cash equities at the ₹1L validation level.
**Rationale:** The architecture's capital analysis says Bank Nifty futures consume too much margin and leave inadequate stop-loss room.
**Alternatives:** F&O at higher capital is described but not implemented.
**Consequences:** Five equities are configured.
**Files:** `docs.txt`, `settings.yaml`, `core/data.py`.

## D4 — Pullback entry over raw breakout
**Decision:** Use a two-phase breakout/setup followed by pullback confirmation.
**Rationale:** The architecture considers raw breakout entry vulnerable to institutional stop-hunts and accepts missing fast no-pullback trends as a tradeoff.
**Alternatives:** Raw breakout entry.
**Consequences:** Missed fast trends are an explicit weakness (W12).
**Files:** `docs.txt`, `core/signals.py`.

## D5 — ATR target and cost-aware trailing stop
**Decision:** Stop = ATR×1.5, target = ATR×2.5; trail moves stop to entry + cost-per-share at the trigger.
**Rationale:** Exact breakeven ignores transaction costs.
**Alternatives:** Stop at exact entry was explicitly rejected by the documented correction.
**Consequences:** The trail seeks zero net rather than zero gross P&L.
**Files:** `docs.txt`, `settings.yaml`, `core/risk.py`.

## D6 — 0.6% minimum gross target
**Decision:** Skip trades below the 0.6% gross target threshold.
**Rationale:** Documented cost math does not support smaller moves.
**Alternatives:** UNKNOWN — not documented.
**Consequences:** Small-ATR trades are rejected before sizing.
**Files:** `docs.txt`, `settings.yaml`, `core/risk.py`.

## D7 — Sector index for relative strength
**Decision:** Use the relevant sector index rather than Nifty 50 for relative strength.
**Rationale:** Explicit architecture decision.
**Alternatives:** Nifty 50 context.
**Consequences:** Code resolves sector context through NFO FUTIDX proxies when needed.
**Files:** `docs.txt`, `core/data.py`, `core/features.py`.

## D8 — ₹1L is validation capital, not return-generation capital
**Decision:** Use ₹1L to validate live behavior; scale only after validation.
**Rationale:** The architecture states the expected return is only about 1–2.4% and defines validation/API discovery/trade-record building as the purpose.
**Alternatives:** Immediate scaling.
**Consequences:** Phase 0 paper trading precedes Phase 1 live capital; Phase 2 requires confirmed live metrics.
**Files:** `docs.txt`, `settings.yaml`.

## W18 — Prior-session RVOL only
**Decision:** RVOL_ToD denominator uses prior sessions only.
**Rationale:** Full-dataset averages create future leakage and can inflate backtest performance.
**Alternatives:** Full-dataset time-slot averages are explicitly rejected by the architecture.
**Consequences:** `RVOLCalculator` uses prior sessions; pipeline adds warmup days.
**Files:** `docs.txt`, `core/data.py`.

## W19 — Bar-close timestamp normalization
**Decision:** Normalize feeds to bar-close timestamps before feature computation.
**Rationale:** Prevents silent stock/sector timestamp misalignment.
**Alternatives:** Provider-native timestamps.
**Consequences:** Data conversion uses UTC parsing, IST conversion and a five-minute shift.
**Files:** `docs.txt`, `core/data.py`.

## W22 — Missing-bar detection
**Decision:** Compare expected versus actual session bars and log gap-affected sessions.
**Rationale:** Missing bars can corrupt VWAP, OR identification and state counters.
**Alternatives:** Ignore gaps.
**Consequences:** Detection exists, but the architecture's final forward-fill/skip-session policy is not fully implemented.
**Files:** `docs.txt`, `core/data.py`.

## W23 — Live execution timing measurement
**Decision:** Measure broker/API latency and resulting slippage during paper trading.
**Rationale:** Backtest assumes next-bar-open fills while live submission can occur later.
**Alternatives:** Assume next-bar-open execution is exact.
**Consequences:** A dedicated measurement implementation is still missing.
**Files:** `docs.txt`, `core/paper_trading.py`.

## Cron + JSON paper trading
**Decision:** Use short-lived cron invocations with persisted `paper_state.json` rather than a long-running process.
**Rationale:** The paper-trading module documents crash recovery and five-minute-bar compatibility.
**Alternatives:** Long-running supervisor-managed loop.
**Consequences:** Each run reconstructs the engine and depends on filesystem state.
**Files:** `core/paper_trading.py`, `run_paper_trading.py`.

## Fresh live slice separate from historical cache
**Decision:** Paper trading uses `fetch_fresh_today()` rather than the ordinary cache-TTL path.
**Rationale:** Repository history documents stale intraday snapshots when the historical TTL was reused for paper trading.
**Alternatives:** Reuse historical cache.
**Consequences:** Each paper cycle performs fresh stock/sector requests.
**Files:** `core/data.py`, `core/paper_trading.py`.

## Shared daily risk across instruments
**Decision:** Backtesting uses one account-wide daily loss budget.
**Rationale:** The code models one ₹1L account, not separate per-instrument accounts.
**Alternatives:** Per-instrument daily limits.
**Consequences:** Losses in one instrument reduce remaining daily budget for others.
**Files:** `core/backtest.py`, `core/risk.py`.

## Conservative same-bar exit ordering
**Decision:** For ambiguous OHLC bars, apply trail update, then stop, then target.
**Rationale:** A 5-minute OHLC bar cannot reveal intrabar order; the implementation deliberately chooses the conservative path.
**Alternatives:** Target-first or another convention.
**Consequences:** Backtest avoids overstating edge on ambiguous bars.
**Files:** `core/risk.py`.

## Daily ScripMaster/token caching
**Decision:** Cache ScripMaster and selected sector futures tokens locally for the day.
**Rationale:** Avoids repeatedly downloading large ScripMaster data and repeatedly resolving the same daily contract.
**Alternatives:** Resolve on every process.
**Consequences:** Cache refresh depends on daily metadata validity.
**Files:** `core/data.py`.
