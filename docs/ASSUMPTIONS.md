# Assumptions

- The supplied technical guide is the authoritative project specification and is copied verbatim to `docs/SPEC.md`.
- The workspace was not initialized as a Git repository, and `uv` is not installed. No repository initialization, package installation, or commit is performed as part of this bootstrap.
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
