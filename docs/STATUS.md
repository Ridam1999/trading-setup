# Project status

Last updated: 2026-11-18

## Phase summary

| Phase | Status | Notes |
| --- | --- | --- |
| 0. Bootstrap | Complete | Configuration, CLI, logging, pinned environment, and repository setup. |
| 1. Data | Loaded; partitioned health check and PIT tooling complete | Imported EOD2 history through 2026-11-13: 7,861,421 rows across 3,733 symbols, 1995-01-02 onward, including NIFTYBEES. The full check completes in about 20 seconds across 32 year partitions and writes HTML plus row-level CSV. It reports zero criticals in the configured post-2005 tradable universe; 38,078 warnings remain, principally older/index-series structural issues and missing sessions. Phase 1 also adds `data import-pit`, corporate-action adjustment, dated membership loading, source priority, and `data verify-pit` with fixture tests. No licensed PIT data is supplied; EOD2 remains survivor-biased and lacks dividend/right adjustments. |
| 2. Event study | Train/validation complete; all setups rejected | ETF and all three stock setups ran on both non-holdout splits, plus a seeded `random_signal` negative control on train. Every horizon is rejected; no setup is approved or provisionally passed under `SURVIVOR_BIASED`. The vectorized event study reduced a 3-year stock slice from 58.4s to 6.7s. Holdout was not accessed. |
| 3. Portfolio backtest | Implementation complete; real-data run pending | Daily simulator, risk sizing, exits, metrics, walk-forward selection, stress tests, Monte Carlo, and Plotly reports are implemented. Synthetic acceptance tests pass. |
| 4. Paper trading | Implementation complete; real-data no-signal run verified | Scanner, next-session fills, exits, SQLite ledger, deterministic markdown/JSON reports, decay monitor, and idempotent `paper run` CLI are implemented. Session bars are loaded only for the signal date and pending-order fill dates; preparing the full 7.86M-row history takes about 1.6s and materializing one session about 0.5s. The 2026-11-13 run produced a no-signal report and an idempotent second run left all artifacts byte-identical. |
| 5. UI and reports | Not started | |
| 6. Broker interface | Not started | Live order submission remains out of scope and disabled. |

## Verification

- make check: passed after the event-study, paper-run, and PIT-data changes.
- Ruff lint and formatting: passed.
- Mypy: passed across 63 source files.
- Pytest: 72 passed, including EOD2 import, quality checks, capped-cost hand arithmetic, bias gates, point-in-time universe ranking, vectorized fill/cost equivalence, negative-control determinism, portfolio reconciliation, paper idempotency, no-signal, next-session fill, delayed-fill, causality, membership alignment, and decay tests, plus 11 PIT tests covering action compounding, before-ex-date adjustment, raw-close preservation, validation, membership dates, source priority, and date-bounded reads.
- Real-data event studies: 50 trials logged; `study report` reports 0 approved setups and 0 provisional passes.
- Real-data paper run: `paper run --date 2026-11-13` completed with 0 signals, 0 orders, and 0 fills; both output files were byte-identical after a repeat run and the ledger still has one `runs` row.
- Phase 3 commit: a91c999 (Implement Phase 3 portfolio backtests).

## Current review and prerequisites

The EOD2 directory was obtained and imported into the local store. The configured splits are non-overlapping: train 2005–2017, validation 2018–2022, and locked holdout 2023 onward. Holdout has not been read.

`reports/data_health.html` summarizes the full history and `reports/data_health.csv` contains row-level findings. There are zero criticals after classifying pre-2005 observations (outside the configured study range) and configured non-equity index names as warnings. Remaining warnings include 4,797 OHLC-range, 45 nonpositive-price, and 5 zero-volume rows; 21 large moves; 32,110 liquidity-screened missing sessions; and 744 stale closes. The large-move list includes `CCAVENUE` on 2026-10-19; the spot-check confirmed a 10:1 share consolidation effective 2026-10-13 with trading resumed 2026-10-19, so EOD2 did not back-adjust those bars and returns spanning the date are invalid.

`reports/import_skipped_rows.csv` accounts for all 101,237 skipped rows in 47 files. The missing required fields were open/high/low (51,481 rows), volume (31,804), and open/high/low/volume together (17,952). No rows were skipped for missing delivery data; delivery fields remain optional.

Groww rates checked on 2026-11-16 are encoded in `config/costs.yaml`, including ₹5–₹20 / 0.1% per-order brokerage, IPFT and ₹20 sale DP. GST covers brokerage, exchange, SEBI, IPFT, and DP charges, not STT/stamp duty. A hand check for a ₹1,000 buy gives ₹7.088586 in charges; a ₹1,000 sell gives ₹30.538586 under the configured male-account DP assumption. Slippage remains a configured 10 bps assumption, not a verified Groww rate.

`config/research.yaml` sets `data_bias_status: SURVIVOR_BIASED`. Study reports may show `provisional_pass`, never `approved`, and the approval YAML is empty. The ETF timing setup covers NIFTYBEES, BANKBEES, JUNIORBEES, LIQUIDBEES, GOLDBEES, ITBEES, and PHARMABEES. ETF, stock, and negative-control trials are summarized in `reports/study_report.html`. The seeded `random_signal` control failed every horizon as required. The five earlier errored `pullback_uptrend` train trials remain in the append-only registry; the report uses the later successful trials.

ETF timing results at 2x modeled costs (expectancy per event):

| Horizon | Train events / dates | Train expectancy | Validation events / dates | Validation expectancy |
| ---: | ---: | ---: | ---: | ---: |
| 1 day | 4 / 4 | -0.45% | 35 / 27 | +0.37% |
| 3 days | 4 / 4 | +0.85% | 35 / 27 | +2.81% |
| 5 days | 4 / 4 | +0.86% | 35 / 27 | +3.23% |
| 10 days | 4 / 4 | +1.95% | 35 / 27 | +3.78% |
| 20 days | 4 / 4 | +3.37% | 35 / 27 | +4.08% |

Stock kill-stage results at 2x modeled costs (ranges across the 1–20 day horizons; event/date counts are for the 1-day horizon):

| Setup | Train events / dates | Train expectancy range | Validation events / dates | Validation expectancy range | Outcome |
| --- | ---: | ---: | ---: | ---: | --- |
| `pullback_uptrend` | 31,526 / 2,812 | -1.04% to -0.33% | 12,111 / 1,132 | -0.94% to -0.67% | rejected |
| `breakout_52w` | 126 / 110 | -1.09% to +0.76% | 30 / 29 | -0.06% to +1.39% | rejected (validation dates below 30) |
| `vol_contraction` | 3,463 / 1,251 | -1.21% to -0.82% | 995 / 393 | -1.22% to -0.39% | rejected |
| `random_signal` (negative control) | 5,627 / 2,633 | -1.08% to -0.46% | — | — | rejected |

ETF samples remain small: train has only 4 independent dates and validation has 27. Stock samples are larger but all setups are cost-negative in train or validation at 2x costs. None constitutes approval, and the broad history remains biased.

EOD2 remains unsuitable as authoritative stock backtest history: it is not intended for backtesting, removes inactive/delisted stocks after a year, and excludes dividend/right-action adjustments. Treat failed setups on this data as kill-stage evidence only; any pass requires point-in-time, delisted-inclusive, total-return history to confirm.

Phase 1 now has the import and verification path for that history, but no licensed dataset has been supplied. `data import-pit --path <raw bars> --actions <actions.csv> --membership <membership.csv>` applies configured action factors and stores dated membership; PIT bars override overlapping EOD2 rows. `data verify-pit --path <raw bars>` writes `reports/pit_verification.json` and exits non-zero when required coverage is missing. Fixture tests demonstrate the CCAVENUE 10:1 consolidation adjustment, delisted-symbol membership, and the failure path. Both commands leave `research.data_bias_status` untouched; switching to `UNBIASED` is a separate owner review recorded in `docs/REAL_DATA_RUNBOOK.md`.
