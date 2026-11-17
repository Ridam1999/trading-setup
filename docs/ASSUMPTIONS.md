# Assumptions

- The supplied technical guide is the authoritative project specification and is copied verbatim to `docs/SPEC.md`.
- At the initial Phase 0 inspection, the workspace had not yet been initialized as a Git repository and `uv` was unavailable. The repository and isolated project environment were subsequently created.
- Dependency versions in `pyproject.toml` are pinned. Phase 1 NumPy is pinned to 2.4.6 because the newer 2.5 line requires Python 3.12 while the specification supports Python 3.11.
- Phase 0 supplies a minimal Pydantic YAML config loader, Typer CLI, and logging setup. It does not implement Phase 1+ behavior.
- Phase 1 treats EOD2 close data as already adjusted and records NSE bhavcopy close in `close_raw`. The calendar models Monday-Friday sessions and relies on the configured holiday list; exceptional weekend sessions must be added explicitly in a later calendar enhancement.
- Universe membership is static unless point-in-time dates are supplied. The default empty symbol list is marked survivorship-biased.
- Phase 2 uses the adjusted `close` series for indicator, entry, and exit calculations; `close_raw` is retained only for comparison. The event-study baseline uses same-date, same-liquidity-filter eligible rows, and its regime-matched version pools eligible rows with the same benchmark trend/volatility labels.
- Setup sweep grids are finite YAML-declared values. A setup remains unapproved until the required neighbor values and train, validation, and explicitly unlocked holdout trials exist in the append-only registry.
# Phase 3 portfolio assumptions

- Walk-forward candidates and parameter values come only from the checked-in setup YAML files and their declared sweep grids. Each training window selects the highest in-sample Sharpe candidate; ties resolve by setup name and parameter order. The selector sees training rows only.
- Portfolio orders are ranked by prior-close traded value (close times volume), descending, with setup name and symbol as stable tie-breaks. Positions are sized to the smaller of fixed fractional stop risk, configured per-symbol weight, configured per-sector weight, and available cash.
- ATR times the configured multiple defines the stop distance when a positive ATR can be computed from pre-signal bars. The setup's configured stop percentage is the fallback. Targets use the setup target or the configured default.
- Stop checks precede target checks. A gap through the stop exits at the open. Circuit-locked bars cannot fill entries or exits; any remaining position is marked to market until an executable bar or the next available time exit.
- Open positions are closed at the final available bar when it is executable. If the final bar is circuit locked, the portfolio curve marks the position at close and the trade remains open.
- Commission and slippage stress runs replay the full portfolio with both configured order charges and slippage scaled by the same multiplier. Illustrative charge rates remain unverified.
- Monte Carlo resamples completed trade returns independently with replacement. It is a simple trade-sequence sensitivity summary and does not model changing market regimes or overlapping portfolio exposure.
- A static sector map may be added to config/backtest.yaml. Symbols without a sector assignment are capped individually rather than grouped into one synthetic sector.
- The backtest CLI permits train and validation only. Holdout remains inaccessible through the portfolio command.

# Phase 4 paper-trading assumptions

- Work resumed at Phase 4 because Phases 0–3 have implementations and Phases 4–6 were stubs. Phases 5 and 6 were not started.
- A paper run date is the signal date. Signals use data through that date's close; the resulting limit orders are filled from the first available session after the signal date, so skipped calendar days catch up without reading future bars.
- Order plans are sized at the signal close using ATR computed only from prior bars. Stops and targets are fixed when the plan is created; the fill price may differ. This keeps the JSON order plan and the ledger consistent.
- The decay monitor uses the worst single closed-trade return as its drawdown proxy because approvals store per-trade summaries rather than an equity curve. Portfolio-level marked drawdown is shown in the report for information only.
- `paper run` validates the data slice it consumes but does not rerun the full multi-year health report; `nse-engine data check` remains the authoritative quality gate.
- An empty `approved_setups.yaml` is a valid no-trade state. Paper reports contain no timestamps so a repeated idempotent run reproduces identical files.

# Real-data verification assumptions

- `random_signal` draws are hashes of `(seed, symbol, session date)` only. They are deterministic, independent of OHLCV history, and expected to fail every gate.
- The point-in-time stock universe ranks symbols by trailing median traded value using only data available on each date; whitespace-containing EOD2 index labels are excluded by configuration.
- `CCAVENUE` had a 10:1 share consolidation effective 2026-10-13, with trading resumed 2026-10-19. EOD2 did not back-adjust the bars, so returns spanning that date are excluded from interpretation.
- The event-study fast path vectorizes the same fill and charge rules as the single-event simulator; equivalence is covered by tests, and registry trials are appended rather than overwritten.
- Paper runs materialize session bars only for the signal date and the first observed session after each pending order. Delayed runs therefore still fill from the first session after the order date, and future bars cannot affect earlier fills.
