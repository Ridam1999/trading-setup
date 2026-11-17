# Project status

Last updated: 2026-11-16

## Phase summary

| Phase | Status | Notes |
| --- | --- | --- |
| 0. Bootstrap | Complete | Configuration, CLI, logging, pinned environment, and repository setup. |
| 1. Data | Loaded; health review incomplete | Imported EOD2 history through 2026-11-13: 7,861,421 rows across 3,733 symbols, 1995-01-02 onward, including NIFTYBEES. Import skipped 101,237 incomplete OHLCV rows from 47 files. The full health check did not complete in a practical time; a 2025–26 slice had zero criticals but many sparse-symbol warnings and one >100% move requiring review. EOD2 itself warns its data is not intended for backtesting and omits delisted symbols after inactivity, dividends, and rights adjustments. |
| 2. Event study | Implementation complete; real-data research blocked | Setups, statistics, gates, reports, and the audited holdout lock are implemented. Do not run studies until the data limitations and quality warnings are resolved; no approved setup file exists. |
| 3. Portfolio backtest | Implementation complete; real-data run pending | Daily simulator, risk sizing, exits, metrics, walk-forward selection, stress tests, Monte Carlo, and Plotly reports are implemented. Synthetic acceptance tests pass. |
| 4. Paper trading | Implementation complete; real-data run pending | Scanner, next-session fills, exits, SQLite ledger, deterministic markdown/JSON reports, decay monitor, and idempotent `paper run` CLI are implemented. The approval registry is empty, so the current state is a verified no-signal run. |
| 5. UI and reports | Not started | |
| 6. Broker interface | Not started | Live order submission remains out of scope and disabled. |

## Verification

- make check: passed after Phase 3 changes.
- Ruff lint and formatting: passed.
- Mypy: passed across 57 source files.
- Pytest: 37 passed, including EOD2 directory import, quality-check behavior, synthetic portfolio reconciliation, and deterministic walk-forward coverage.
- Phase 3 commit: a91c999 (Implement Phase 3 portfolio backtests).

## Current review and prerequisites

The EOD2 directory was obtained and imported into the local store. The configured splits are non-overlapping: train 2005–2017, validation 2018–2022, and locked holdout 2023 onward. No setup studies or holdout reads have been run.

The recent two-year health slice produced 47,662 warnings: 46,288 missing observed sessions (concentrated in sparse listings), 1,373 repeated-close flags, and one large move (`CCAVENUE`, 2026-10-19). There were zero critical findings in that slice. The full-history check was stopped after several minutes without a report. The checker needs a scalable summary and symbol-level review before Phase 2.

Current Groww pricing was checked on 2026-11-16. It lists ₹20 / 0.1% brokerage with a ₹5 minimum, NSE transaction charge 0.00307%, SEBI 0.0001%, IPFT 0.0001%, 0.1% delivery STT on both sides, 0.015% buy stamp duty, and sale DP charges of ₹20 plus GST for the usual debit value. The current cost model cannot represent brokerage minimum/maximum or IPFT and does not apply GST to DP charges. `config/costs.yaml` remains illustrative and must not be used to interpret performance until the model is corrected and rates are annotated.

No random-signal negative control is registered. EOD2 is unsuitable as authoritative backtest history: its data notes say it is not intended for backtesting, removes inactive/delisted stocks after a year, and excludes dividend/right-action adjustments. Use a point-in-time, delisted-inclusive source with total-return corporate-action treatment before proceeding to real-data studies. See docs/REAL_DATA_RUNBOOK.md for source notes and the held steps.
