# Project status

Last updated: 2026-11-16

## Phase summary

| Phase | Status | Notes |
| --- | --- | --- |
| 0. Bootstrap | Complete | Configuration, CLI, logging, pinned environment, and repository setup. |
| 1. Data | Implementation complete; data load pending | Import, storage, calendar, and health-check tooling are implemented. EOD2 per-symbol directory import is supported. The adjusted data store currently contains no market history. |
| 2. Event study | Implementation complete; real-data research pending | Setups, statistics, gates, reports, and the audited holdout lock are implemented. No approved setup file exists until the studies pass on supplied data. |
| 3. Portfolio backtest | Implementation complete; real-data run pending | Daily simulator, risk sizing, exits, metrics, walk-forward selection, stress tests, Monte Carlo, and Plotly reports are implemented. Synthetic acceptance tests pass. |
| 4. Paper trading | Not started | |
| 5. UI and reports | Not started | |
| 6. Broker interface | Not started | Live order submission remains out of scope and disabled. |

## Verification

- make check: passed after Phase 3 changes.
- Ruff lint and formatting: passed.
- Mypy: passed across 57 source files.
- Pytest: 36 passed, including EOD2 directory import, synthetic portfolio reconciliation, and deterministic walk-forward coverage.
- Phase 3 commit: a91c999 (Implement Phase 3 portfolio backtests).

## Manual prerequisite

To run the research pipeline on real market history, import adjusted daily OHLCV that includes the configured benchmark NIFTYBEES. The EOD2 directory importer now accepts its per-symbol CSV folder. Then run the Phase 2 setup studies and review their approval gates to produce config/approved_setups.yaml. The portfolio CLI intentionally refuses to run approved-setup simulations without that file and does not read holdout data.

The suggested random_signal negative-control setup has not yet been added. See docs/REAL_DATA_RUNBOOK.md for the current command sequence and this gap.

Illustrative transaction-cost rates in config/costs.yaml also need to be verified before interpreting results.
