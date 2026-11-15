# Real-data research runbook

This adapts the EOD2 sequence to the commands and gates implemented in this repository. Do not interpret research results until the local data, cost assumptions, and approval gates have been reviewed.

## 1. Prepare adjusted EOD2 history

Install EOD2 outside this repository. Its current wiki describes either downloading the project archive and running setup_data.py, or cloning with its data submodule; it stores per-symbol files under src/eod2_data/daily. See the [EOD2 installation guide](https://github.com/BennyThadikaran/eod2/wiki/Installation) and [usage guide](https://github.com/BennyThadikaran/eod2/wiki/Usage).

Check that the folder contains recent data and a benchmark file for NIFTYBEES. The EOD2 wiki confirms daily per-symbol files and split/bonus adjustments, but it does not confirm the local benchmark filename. If NIFTYBEES is absent, inspect the available index files and update both config/study.yaml and config/backtest.yaml to the exact imported benchmark symbol.

## 2. Import and inspect data

The importer accepts a single CSV or the per-symbol directory. For the directory:

```sh
uv run --no-sync nse-engine data import-eod2 --path /path/to/eod2/src/eod2_data/daily
uv run --no-sync nse-engine data check
uv run --no-sync nse-engine data summary
```

The directory importer uses each filename as its symbol when the file has no symbol column. The summary reports whether the configured benchmark was found.

Open reports/data_health.html. Critical structural errors make data check exit nonzero and must be resolved before research. Review warnings separately, including large one-day moves, missing observed sessions, stale closes, adjusted/raw differences, and year-by-year symbol coverage. EOD2-only imports do not contain an independent raw-close series, so the adjusted/raw comparison cannot verify corporate actions unless raw data has also been imported. Spot-check important split and bonus dates before trusting the adjusted history.

## 3. Verify local assumptions

- Verify every rate in config/costs.yaml against current broker, exchange, and SEBI sources. Add a source and verification date beside each edited rate. Current values are illustrative.
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

There is currently no registered random_signal negative-control setup. The supplied workflow's Step 5 therefore needs that setup added and verified before treating a full real-data run as validated. Do not substitute a real setup and call it a negative control.

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
