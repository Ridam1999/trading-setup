# NSE Setup Research Engine

Local, deterministic research tooling for studying rule-based NSE equity setups. Research output only; it is not investment advice and does not guarantee profit. Live trading is out of scope for the initial phases.

## Phase 0: Bootstrap

Python 3.11+ is required. Install `uv`, then run:

```sh
make setup
uv run --no-sync nse-engine config-check
make check
```

The setup target builds the pinned dependencies and project wheel in `.venv`. The CLI reads YAML files from `config/`. Set `NSE_ENGINE_CONFIG_DIR` to use another config directory.

### Phase 1: daily data

```sh
uv run --no-sync nse-engine data import-eod2 --path /path/to/eod2_data/daily
uv run --no-sync nse-engine data update --path /path/to/bhavcopy.zip
uv run --no-sync nse-engine data check
uv run --no-sync nse-engine data summary
```

`data update` also accepts `--url` or `nse_bhavcopy_url` in `config/data.yaml`; downloads retry and are written only after a non-empty response. Data is stored under `data/adjusted/` as year-partitioned Parquet and in `data/duckdb/nse.duckdb`. The health report is written to `reports/data_health.html`. For daily updates, configure a current NSE bhavcopy URL or provide a local downloaded file. Weekdays are treated as sessions except dates in `config/data.yaml` `holidays`; update that list for exchange holidays and special sessions.

`data import-eod2` accepts either one CSV or a directory of per-symbol CSV files. For EOD2's per-symbol files, the importer uses each filename as the symbol when no ticker column is present. `data summary` reports whether the configured benchmark (NIFTYBEES by default) was imported.

The point-in-time (PIT) upgrade requires licensed inputs and never changes the bias flag by itself:

```sh
uv run --no-sync nse-engine data import-pit --path /path/to/pit_bars.csv --actions /path/to/actions.csv --membership /path/to/membership.csv
uv run --no-sync nse-engine data verify-pit --path /path/to/pit_bars.csv
```

`import-pit` reads raw OHLCV bars (including delisted symbols), applies each CSV's positive `price_factor` to bars strictly before its `ex_date` (volume is divided by the same factor), preserves `close_raw`, and stores dated membership. Paths may instead be set as `corporate_actions_path` and `pit_membership_path` in `config/data.yaml`. PIT bars take precedence over EOD2 bars for the same symbol and date. `verify-pit` writes `reports/pit_verification.json`, exits non-zero if required coverage is missing, and never edits `research.data_bias_status`; the owner must review the evidence and change that YAML value manually. No licensed PIT dataset is supplied here, so this repository remains `SURVIVOR_BIASED`.

### Phase 2: event studies

```sh
uv run --no-sync nse-engine study run --setup pullback_uptrend --split train
uv run --no-sync nse-engine study sweep --setup pullback_uptrend --split train
uv run --no-sync nse-engine study run --setup random_signal --split train
uv run --no-sync nse-engine study report
```

For the free-data kill stage, add `--provisional` to `study run`, `study sweep`, and `study report`:

```sh
uv run --no-sync nse-engine study run --setup etf_timing --split train --provisional
uv run --no-sync nse-engine study sweep --setup pullback_uptrend --split train --provisional
uv run --no-sync nse-engine study sweep --setup delivery_spike --split train --provisional
uv run --no-sync nse-engine study sweep --setup relative_strength_regime --split train --provisional
uv run --no-sync nse-engine study report --provisional
```

`delivery_spike` requires `delivery_pct`, which is null throughout the local EOD2 import; on this store it is a data-coverage rejection, not a verdict on the hypothesis. `relative_strength_regime` compares a stock's trailing return with the configured benchmark's and requires a benign benchmark regime; the benchmark close is injected by the event builder as `_benchmark_close`.

Provisional mode is train/validation only: it rejects `--split holdout`, screens stock setups with the top-100 point-in-time liquidity universe in `config/universe_provisional.yaml`, and tags its registry rows so the report uses only those trials. Gates return `provisional_pass` or `rejected`, never `approved`; `approved_setups.yaml` is not written. The report is `reports/study_report_provisional.html` with a prominent `SURVIVOR_BIASED` banner. A failed setup is discarded; a pass is only a candidate for later verification.

Studies append every setup/parameter/split/horizon result to the SQLite registry at `data/duckdb/trials.sqlite` and save per-event Parquet files in `reports/study_events/`. The report applies Benjamini-Hochberg correction across logged trials and evaluates configured approval gates. `random_signal` is a seeded negative control with no price dependence; it must fail the gates. Holdout data stays locked; accessing it requires `--unlock-holdout`, which adds an audit row to the registry. With `research.data_bias_status: SURVIVOR_BIASED`, a statistical pass is provisional only and cannot populate approved_setups.yaml.

### Phase 3: portfolio backtests

Run an approved setup portfolio on a fixed, non-holdout split:

```sh
uv run --no-sync nse-engine backtest run --split train
uv run --no-sync nse-engine backtest run --split validation
uv run --no-sync nse-engine backtest walk-forward --split train
uv run --no-sync nse-engine backtest walk-forward --split validation
```

Backtests read only configurations listed in config/approved_setups.yaml; generate and review that file with nse-engine study report first. Walk-forward runs use the declared setup parameter grids, choose the best Sharpe setup on each past-only training window, then test on the next window. Portfolio limits and rolling windows live in config/backtest.yaml. Each run writes a uniquely named folder under reports/ containing an HTML Plotly report and Parquet equity, order, trade, and (for walk-forward) window tables. Backtest experiments append to the same immutable SQLite trial registry.

### Phase 4: paper trading

Run the daily scanner after the evening data update:

```sh
uv run --no-sync nse-engine paper run
uv run --no-sync nse-engine paper run --date 2026-11-18
uv run --no-sync nse-engine paper run --date 2026-11-18 --data-path /path/to/bhavcopy.zip
```

The verified 2026-11-13 run wrote `reports/paper/paper_2026-11-13.md` and `reports/paper/orders_2026-11-13.json` with zero signals, orders, and fills; a repeat run left both files byte-identical and the ledger at one `runs` row. `paper run` is idempotent per signal date: a repeated date re-emits the same report and JSON order plan without duplicating ledger rows. It evaluates only setups listed in `config/approved_setups.yaml`, skips setups suspended by the decay monitor, and fills pending orders from the next available session using the shared limit-order model. If no setup fires (including when the approval file is empty), it still writes a report saying so. Reports and order plans go to `reports/paper/`; the ledger is `data/duckdb/paper.sqlite`. Paths and decay thresholds live in `config/paper.yaml`. Every report carries the research disclaimer and the configured data-bias status. No broker or live-order code is involved.

## Project phases

0. Bootstrap: complete.
1. Data: EOD2 market history loaded with partitioned health checks; PIT import, corporate-action adjustment, and verification commands added. Source remains survivor-biased until licensed PIT data is supplied and reviewed.
2. Event study: free-data provisional kill stage complete on train/validation; all eight setups (ETF and stock, plus a seeded random-signal control) rejected across 100 gate groups, with 0 provisional passes and 0 approvals. Holdout remains locked and `SURVIVOR_BIASED` is unchanged.
3. Portfolio backtest: implementation complete; synthetic acceptance passes, real-data run pending.
4. Paper trading: implementation complete; the optimized real-data no-signal run is verified, and approvals remain empty.
5. UI and reports: not started.
6. Broker interface: not started; live orders remain disabled.

See [docs/STATUS.md](docs/STATUS.md) for verification and manual prerequisites, [docs/REAL_DATA_RUNBOOK.md](docs/REAL_DATA_RUNBOOK.md) for the adapted EOD2 workflow, and [docs/SPEC.md](docs/SPEC.md) for requirements and acceptance criteria.
