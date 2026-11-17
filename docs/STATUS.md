# Project status

Last updated: 2026-11-16

## Phase summary

| Phase | Status | Notes |
| --- | --- | --- |
| 0. Bootstrap | Complete | Configuration, CLI, logging, pinned environment, and repository setup. |
| 1. Data | Loaded; partitioned health check complete | Imported EOD2 history through 2026-11-13: 7,861,421 rows across 3,733 symbols, 1995-01-02 onward, including NIFTYBEES. The full check completes in about 20 seconds across 32 year partitions and writes HTML plus row-level CSV. It reports zero criticals in the configured post-2005 tradable universe; 38,078 warnings remain, principally older/index-series structural issues and missing sessions. EOD2 is still survivor-biased and lacks dividend/right adjustments. |
| 2. Event study | ETF train/validation complete; stock kill-stage pending | `etf_timing` ran on train and validation only. Train had 4 events across 4 dates; validation had 35 events across 27 dates, below the 30-date gate. The history is marked `SURVIVOR_BIASED`, so no setup can be approved and approved_setups.yaml remains empty. A full stock train run exceeded six minutes without producing split metrics and was stopped; optimize it before retrying the remaining stock setups. Holdout was not accessed. |
| 3. Portfolio backtest | Implementation complete; real-data run pending | Daily simulator, risk sizing, exits, metrics, walk-forward selection, stress tests, Monte Carlo, and Plotly reports are implemented. Synthetic acceptance tests pass. |
| 4. Paper trading | Implementation complete; real-data run pending | Scanner, next-session fills, exits, SQLite ledger, deterministic markdown/JSON reports, decay monitor, and idempotent `paper run` CLI are implemented. The approval registry is empty, so the current state is a verified no-signal run. |
| 5. UI and reports | Not started | |
| 6. Broker interface | Not started | Live order submission remains out of scope and disabled. |

## Verification

- make check: passed after bias, cost, point-in-time universe, ETF setup, and paper-trading changes.
- Ruff lint and formatting: passed.
- Mypy: passed across 60 source files.
- Pytest: 48 passed, including EOD2 import, quality checks, capped-cost hand arithmetic, bias gates, point-in-time universe ranking, portfolio reconciliation, and paper idempotency, no-signal, next-session fill, causality, membership alignment, and decay tests.
- Phase 3 commit: a91c999 (Implement Phase 3 portfolio backtests).

## Current review and prerequisites

The EOD2 directory was obtained and imported into the local store. The configured splits are non-overlapping: train 2005–2017, validation 2018–2022, and locked holdout 2023 onward. Holdout has not been read.

`reports/data_health.html` summarizes the full history and `reports/data_health.csv` contains row-level findings. There are zero criticals after classifying pre-2005 observations (outside the configured study range) and configured non-equity index names as warnings. Remaining warnings include 4,797 OHLC-range, 45 nonpositive-price, and 5 zero-volume rows; 21 large moves; 32,110 liquidity-screened missing sessions; and 744 stale closes. The large-move list includes `CCAVENUE` on 2026-10-19, which still needs a corporate-action spot-check.

`reports/import_skipped_rows.csv` accounts for all 101,237 skipped rows in 47 files. The missing required fields were open/high/low (51,481 rows), volume (31,804), and open/high/low/volume together (17,952). No rows were skipped for missing delivery data; delivery fields remain optional.

Groww rates checked on 2026-11-16 are encoded in `config/costs.yaml`, including ₹5–₹20 / 0.1% per-order brokerage, IPFT and ₹20 sale DP. GST covers brokerage, exchange, SEBI, IPFT, and DP charges, not STT/stamp duty. A hand check for a ₹1,000 buy gives ₹7.088586 in charges; a ₹1,000 sell gives ₹30.538586 under the configured male-account DP assumption. Slippage remains a configured 10 bps assumption, not a verified Groww rate.

`config/research.yaml` sets `data_bias_status: SURVIVOR_BIASED`. Study reports may show `provisional_pass`, never `approved`, and the approval YAML is empty. The ETF timing setup covers NIFTYBEES, BANKBEES, JUNIORBEES, LIQUIDBEES, GOLDBEES, ITBEES, and PHARMABEES. ETF train/validation results are recorded in the trial registry and summarized in `reports/study_report.html`; stock kill-stage runs are pending event-study runtime optimization. No random-signal negative control is registered.

ETF timing results at 2x modeled costs (expectancy per event):

| Horizon | Train events / dates | Train expectancy | Validation events / dates | Validation expectancy |
| ---: | ---: | ---: | ---: | ---: |
| 1 day | 4 / 4 | -0.45% | 35 / 27 | +0.37% |
| 3 days | 4 / 4 | +0.85% | 35 / 27 | +2.81% |
| 5 days | 4 / 4 | +0.86% | 35 / 27 | +3.23% |
| 10 days | 4 / 4 | +1.95% | 35 / 27 | +3.78% |
| 20 days | 4 / 4 | +3.37% | 35 / 27 | +4.08% |

These are small, exploratory samples. Train fails the configured 30-independent-date threshold; validation has 27 dates. Neither constitutes approval, and the broad history remains biased.

EOD2 remains unsuitable as authoritative stock backtest history: it is not intended for backtesting, removes inactive/delisted stocks after a year, and excludes dividend/right-action adjustments. Treat failed setups on this data as kill-stage evidence only; any pass requires point-in-time, delisted-inclusive, total-return history to confirm.
