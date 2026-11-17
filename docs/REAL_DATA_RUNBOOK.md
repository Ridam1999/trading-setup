# Real-data research runbook

This adapts the EOD2 sequence to the commands and gates implemented in this repository. Do not interpret research results until the local data, cost assumptions, and approval gates have been reviewed.

## 1. Prepare adjusted EOD2 history

Install EOD2 outside this repository. Its current wiki describes either downloading the project archive and running setup_data.py, or cloning with its data submodule; it stores per-symbol files under src/eod2_data/daily. See the [EOD2 installation guide](https://github.com/BennyThadikaran/eod2/wiki/Installation) and [usage guide](https://github.com/BennyThadikaran/eod2/wiki/Usage).

Check that the folder contains recent data and a benchmark file for NIFTYBEES. The EOD2 wiki confirms daily per-symbol files and split/bonus adjustments, but it does not confirm the local benchmark filename. If NIFTYBEES is absent, inspect the available index files and update both config/study.yaml and config/backtest.yaml to the exact imported benchmark symbol.

**Source limitation:** the [EOD2 data repository](https://github.com/BennyThadikaran/eod2_data) says the data is not intended for backtesting, inactive/delisted stocks are removed after one year, and dividends and rights issues are not adjusted. The local import confirms NIFTYBEES exists, but this does not make the history point-in-time or total-return complete. Treat it as an import-pipeline exercise only, not evidence of a strategy edge.

## 2. Import and inspect data

The importer accepts a single CSV or the per-symbol directory. For the directory:

```sh
uv run --no-sync nse-engine data import-eod2 --path /path/to/eod2/src/eod2_data/daily
uv run --no-sync nse-engine data check
uv run --no-sync nse-engine data summary
```

The directory importer uses each filename as its symbol when the file has no symbol column. The summary reports whether the configured benchmark was found.

Open reports/data_health.html. Critical structural errors make data check exit nonzero and must be resolved before research. Review warnings separately, including large one-day moves, missing observed sessions, stale closes, adjusted/raw differences, and year-by-year symbol coverage. EOD2-only imports do not contain an independent raw-close series, so the adjusted/raw comparison cannot verify corporate actions unless raw data has also been imported. Spot-check important split and bonus dates before trusting the adjusted history.

The 2026-11-16 import contained 7,861,421 rows from 3,733 symbols (1995-01-02 through 2026-11-13), with 101,237 incomplete required OHLCV rows omitted from 47 files. `reports/import_skipped_rows.csv` shows the breakdown: 51,481 missing open/high/low, 31,804 missing volume, and 17,952 missing open/high/low/volume; no delivery field was a skip reason. The partitioned checker now completes in about 20 seconds and writes `reports/data_health.html` plus row-level `reports/data_health.csv`. It reports zero criticals in the post-2005 tradable scope. The >100% CCAVENUE move on 2026-10-19 was spot-checked: AvenuesAI (formerly Infibeam Avenues) executed a 10:1 share consolidation effective 2026-10-13 and resumed trading on 2026-10-19 under a new ISIN. The old close of ₹13.83 maps to roughly ₹138.30 before later price movement, matching the observed ₹135.65 open. EOD2 did not back-adjust for this consolidation, so returns spanning that date are not usable; exclude those bars or add a corporate-action adjustment before any study that includes them.

## 3. Verify local assumptions

- Current Groww charges were checked 2026-11-16 and encoded in `config/costs.yaml`: ₹20 / 0.1% brokerage with ₹5 minimum, NSE transaction charge 0.00307%, SEBI 0.0001%, IPFT 0.0001%, delivery STT 0.1% on buy and sell, buy stamp duty 0.015%, and usual male-account DP ₹20 plus GST on a qualifying sale. The model now handles per-order brokerage bounds, IPFT, and GST on DP. See Groww's [pricing page](https://groww.in/pricing) and official [NSE charges page](https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies). Slippage remains an assumption; confirm the exact DP/charges against your contract note.
- Check config/splits.yaml for non-overlapping ranges. The checked-in defaults are train through 2017, validation 2018–2022, and holdout from 2023 onward.
- Review config/gates.yaml before inspecting performance. Do not relax thresholds after seeing results.
- Keep holdout locked while choosing hypotheses or parameters.

## 4. Run train and validation studies

Run the declared base setup on train and validation. The starter parameter-neighbour gates also require declared neighboring values, which can be run with study sweep. Keep the sweep to the small predeclared YAML grids.

```sh
uv run --no-sync nse-engine study run --setup pullback_uptrend --split train
uv run --no-sync nse-engine study run --setup pullback_uptrend --split validation
uv run --no-sync nse-engine study sweep --setup pullback_uptrend --split train
uv run --no-sync nse-engine study sweep --setup pullback_uptrend --split validation
uv run --no-sync nse-engine study report
```

Repeat for the other starter setups. The report shows each gate, including separate train and validation sample-size checks. Before holdout results exist, the holdout and cross-split neighbor gates necessarily fail; study report also writes an empty approved_setups.yaml at this stage. There is no separate gates command.

Run `study run --setup random_signal --split train` as the seeded negative control: it must fail the gates. The declared stock universe ranks the top 200 by trailing 60-session median traded value on each date, with configured price and history filters; whitespace-containing EOD2 index labels are excluded from this stock universe. Results are kill-stage evidence only: because the source is survivor-biased, the report records 0 approvals and 0 provisional passes.

The completed run logged 50 trials: ETF, `pullback_uptrend`, `breakout_52w`, and `vol_contraction` on train and validation, plus `random_signal` on train. All horizons were rejected. The optimized event study processed the 3-year stock timing slice (426,410 rows, 37,541 events across 657 dates) in 6.7 seconds, down from 58.4 seconds. The five earlier errored `pullback_uptrend` train trials remain in the registry as audit records; later successful trials were appended rather than replacing them.

## 5. Final holdout review

Only after train and validation review is complete, access holdout for the preselected configurations required by the gates. Each explicit holdout study invocation records an audit event. Do not change parameters after reading holdout results.

```sh
uv run --no-sync nse-engine study run --setup pullback_uptrend --split holdout --unlock-holdout
uv run --no-sync nse-engine study sweep --setup pullback_uptrend --split holdout --unlock-holdout
uv run --no-sync nse-engine study report
```

Run the holdout sweep only when needed to evaluate the predeclared adjacent parameter values. Review reports/study_report.html and config/approved_setups.yaml. Zero approved setups is a valid outcome.

## 6. Portfolio evaluation

Only run this after the final approval file contains reviewed setups. The portfolio CLI reads train or validation ranges and does not expose holdout.

```sh
uv run --no-sync nse-engine backtest walk-forward --split train
uv run --no-sync nse-engine backtest walk-forward --split validation
```

Review the stitched out-of-sample curve, cost-stress table, drawdown depth and duration, and benchmark comparison in each timestamped reports/backtest_* folder. Verify transaction costs before interpreting any result.

## 7. Paper run verification

With no approved setups, a real-data paper run is still a valid no-signal check:

```sh
uv run --no-sync nse-engine paper run --date 2026-11-13
```

This produced `reports/paper/paper_2026-11-13.md` and `reports/paper/orders_2026-11-13.json` with zero signals, orders, and fills, and one row in `data/duckdb/paper.sqlite` `runs`. Repeating the command left both files byte-identical and did not add ledger rows. The runner prepares the full 7.86M-row history in about 1.6 seconds and materializes only the sessions it needs (about 0.5 seconds for the signal date) rather than building all 7,886 session dictionaries.
