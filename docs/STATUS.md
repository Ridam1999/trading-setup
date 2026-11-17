# Project status

Last updated: 2026-11-18

## Phase summary

| Phase | Status | Notes |
| --- | --- | --- |
| 0. Bootstrap | Complete | Configuration, CLI, logging, pinned environment, and repository setup. |
| 1. Data | Loaded; partitioned health check and PIT tooling complete | Imported EOD2 history through 2026-11-13: 7,861,421 rows across 3,733 symbols, 1995-01-02 onward, including NIFTYBEES. The full check completes in about 20 seconds across 32 year partitions and writes HTML plus row-level CSV. It reports zero criticals in the configured post-2005 tradable universe; 38,078 warnings remain, principally older/index-series structural issues and missing sessions. Phase 1 also adds `data import-pit`, corporate-action adjustment, dated membership loading, source priority, and `data verify-pit` with fixture tests. No licensed PIT data is supplied; EOD2 remains survivor-biased and lacks dividend/right adjustments. |
| 2. Event study | Free-data provisional kill stage complete; all setups rejected | Eight setups (ETF timing, six stock setups, and a seeded `random_signal` negative control) ran on train and validation via explicit `--provisional` mode, using a point-in-time top-100 liquidity universe for stocks. 200 provisional trials appended; 100 setup/parameter/horizon gate groups all rejected, 0 provisional passes, 0 approvals. `delivery_spike` is a data-coverage rejection: the local EOD2 import has no delivery values. The vectorized event study reduced a 3-year stock slice from 58.4s to 6.7s. Holdout was never accessed and no approval file was written. |
| 3. Portfolio backtest | Implementation complete; real-data run pending | Daily simulator, risk sizing, exits, metrics, walk-forward selection, stress tests, Monte Carlo, and Plotly reports are implemented. Synthetic acceptance tests pass. |
| 4. Paper trading | Implementation complete; real-data no-signal run verified | Scanner, next-session fills, exits, SQLite ledger, deterministic markdown/JSON reports, decay monitor, and idempotent `paper run` CLI are implemented. Session bars are loaded only for the signal date and pending-order fill dates; preparing the full 7.86M-row history takes about 1.6s and materializing one session about 0.5s. The 2026-11-13 run produced a no-signal report and an idempotent second run left all artifacts byte-identical. |
| 5. UI and reports | Not started | |
| 6. Broker interface | Not started | Live order submission remains out of scope and disabled. |

## Verification

- make check: passed after the event-study, paper-run, PIT-data, and both provisional kill-stage rounds.
- Ruff lint and formatting: passed.
- Mypy: passed across 65 source files.
- Pytest: 83 passed, including EOD2 import, quality checks, capped-cost hand arithmetic, bias gates, provisional gates, top-100 provisional universe, approval-file safety, point-in-time universe ranking, vectorized fill/cost equivalence, negative-control determinism, portfolio reconciliation, paper idempotency, no-signal, next-session fill, delayed-fill, causality, membership alignment, decay, benchmark-column injection, and per-setup look-ahead tests, plus 11 PIT tests covering action compounding, before-ex-date adjustment, raw-close preservation, validation, membership dates, source priority, and date-bounded reads.
- Free-data provisional kill stage: 200 provisional trials logged (0 errors), 100 setup/parameter/horizon gate groups all rejected, 0 provisional passes, 0 approvals, holdout untouched. `study report --provisional` writes `reports/study_report_provisional.html` and does not write `approved_setups.yaml`.
- Real-data paper run: `paper run --date 2026-11-13` completed with 0 signals, 0 orders, and 0 fills; both output files were byte-identical after a repeat run and the ledger still has one `runs` row.
- Phase 3 commit: a91c999 (Implement Phase 3 portfolio backtests).

## Current review and prerequisites

The EOD2 directory was obtained and imported into the local store. The configured splits are non-overlapping: train 2005–2017, validation 2018–2022, and locked holdout 2023 onward. Holdout has not been read.

`reports/data_health.html` summarizes the full history and `reports/data_health.csv` contains row-level findings. There are zero criticals after classifying pre-2005 observations (outside the configured study range) and configured non-equity index names as warnings. Remaining warnings include 4,797 OHLC-range, 45 nonpositive-price, and 5 zero-volume rows; 21 large moves; 32,110 liquidity-screened missing sessions; and 744 stale closes. The large-move list includes `CCAVENUE` on 2026-10-19; the spot-check confirmed a 10:1 share consolidation effective 2026-10-13 with trading resumed 2026-10-19, so EOD2 did not back-adjust those bars and returns spanning the date are invalid.

`reports/import_skipped_rows.csv` accounts for all 101,237 skipped rows in 47 files. The missing required fields were open/high/low (51,481 rows), volume (31,804), and open/high/low/volume together (17,952). No rows were skipped for missing delivery data; delivery fields remain optional.

Groww rates checked on 2026-11-16 are encoded in `config/costs.yaml`, including ₹5–₹20 / 0.1% per-order brokerage, IPFT and ₹20 sale DP. GST covers brokerage, exchange, SEBI, IPFT, and DP charges, not STT/stamp duty. A hand check for a ₹1,000 buy gives ₹7.088586 in charges; a ₹1,000 sell gives ₹30.538586 under the configured male-account DP assumption. Slippage remains a configured 10 bps assumption, not a verified Groww rate.

`config/research.yaml` still sets `data_bias_status: SURVIVOR_BIASED`; the flag is a statement of fact about the data and was not changed to unblock the kill stage. The free-data decision runs in explicit `--provisional` mode: train and validation only, a point-in-time top-100 liquidity universe for stock setups (`config/universe_provisional.yaml`), and gates that return `provisional_pass` or `rejected` but never `approved`. Every provisional report prints `SURVIVOR_BIASED` prominently, uses only provisional-tagged registry rows, and never writes or deletes `approved_setups.yaml`. The ETF timing setup covers NIFTYBEES, BANKBEES, JUNIORBEES, LIQUIDBEES, GOLDBEES, ITBEES, and PHARMABEES. The seeded `random_signal` control failed every horizon as required. Earlier standard-mode exploratory trials remain in the append-only registry but are excluded from the provisional report.

### Free-data provisional kill-stage results (2026-11-18)

Run with `study run`/`study sweep --provisional` on train and validation. Stock setups use the top-100 point-in-time traded-value universe in `config/universe_provisional.yaml`; ETF setups keep their configured symbol lists. Costs are modeled at the configured 2x stress multiplier. The table shows ranges across swept parameters and the 1, 3, 5, 10, and 20-day horizons; independent-date counts are per horizon.

| Setup | Trials | Train independent dates | Train expectancy | Validation independent dates | Validation expectancy | Outcome |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `etf_timing` | 10 | 4 | -0.45% to +3.37% | 27 | +0.37% to +4.08% | rejected (dates < 30) |
| `index_dip` | 30 | 0 | no events | 0 | no events | rejected (no signals) |
| `pullback_uptrend` | 30 | 1,853–2,728 | -1.11% to -0.41% | 760–1,104 | -0.97% to -0.65% | rejected (negative expectancy) |
| `breakout_52w` | 30 | 17–28 | -1.51% to +2.88% | 1 | -8.75% to -5.83% | rejected (dates, expectancy) |
| `vol_contraction` | 30 | 441–1,496 | -1.31% to +0.43% | 53–568 | -2.27% to -0.13% | rejected (breadth, stability) |
| `random_signal` (negative control) | 10 | 1,890–1,900 | -1.09% to -0.36% | 716–729 | -1.19% to -0.49% | rejected (negative control) |
| `delivery_spike` (before importer fix) | 30 | 0 | no events | 0 | no events | rejected (delivery fields dropped by importer) |
| `delivery_spike` (after importer fix) | 30 | 0 | no events | 26–468 | -0.55% to +0.55% | rejected (no train coverage; negative expectancy) |
| `relative_strength_regime` | 30 | 1,907–1,926 | -1.12% to -0.94% | 576–595 | -1.52% to -1.16% | rejected (negative expectancy) |

Some groups reached Benjamini–Hochberg adjusted p < 0.05 (notably `etf_timing` at h=3, p≈0.042, and several `vol_contraction` horizons down to p≈0.0009), but every group was rejected on cost-adjusted expectancy, independent-date count, symbol breadth, single-year profit concentration, or parameter-neighbour stability.

The `delivery_spike` nulls were an **importer bug**: real EOD2 CSVs carry `DLV_QTY` and no delivery-percentage column, while the importer only aliased `deliv_qty`/`deliv_per`. The fix maps `DLV_QTY` and derives `delivery_pct` from the same row's volume. After a full re-import, 3,789,692 of 7,861,421 rows (48.21%) have delivery values: 0% before 2019, 24.8% in 2019, and 96.3%–97.4% from 2020 onward. The hypothesis was rerun through the same gates: train (ending 2017) still has no delivery events, and validation expectancy is -0.55% to +0.55% across parameters and horizons. It remains a **coverage rejection** on train, not proof against the accumulation idea. `relative_strength_regime` generated events but was cost-negative on both splits, with adjusted p values between 0.65 and 0.97. No setup produced a `provisional_pass`, so none is a candidate for real-data verification. Because nothing survived, no dataset purchase or build is currently justified.

The append-only registry now holds 280 trials (30 `delivery_spike` rows from the buggy import, 30 from the corrected import, and 220 from the other setups). The provisional report groups by setup/parameters/horizon and shows the latest metrics per split, so the corrected run supersedes the buggy one in the report while the earlier rows remain recorded.

EOD2 remains unsuitable as authoritative stock backtest history: it is not intended for backtesting, removes inactive/delisted stocks after a year, and excludes dividend/right-action adjustments. Treat failed setups on this data as kill-stage evidence only; any pass requires point-in-time, delisted-inclusive, total-return history to confirm.

Phase 1 now has the import and verification path for that history, but no licensed dataset has been supplied. `data import-pit --path <raw bars> --actions <actions.csv> --membership <membership.csv>` applies configured action factors and stores dated membership; PIT bars override overlapping EOD2 rows. `data verify-pit --path <raw bars>` writes `reports/pit_verification.json` and exits non-zero when required coverage is missing. Fixture tests demonstrate the CCAVENUE 10:1 consolidation adjustment, delisted-symbol membership, and the failure path. Both commands leave `research.data_bias_status` untouched; switching to `UNBIASED` is a separate owner review recorded in `docs/REAL_DATA_RUNBOOK.md`.
