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
uv run --no-sync nse-engine data import-eod2 --path /path/to/eod2.csv
uv run --no-sync nse-engine data update --path /path/to/bhavcopy.zip
uv run --no-sync nse-engine data check
uv run --no-sync nse-engine data summary
```

`data update` also accepts `--url` or `nse_bhavcopy_url` in `config/data.yaml`; downloads retry and are written only after a non-empty response. Data is stored under `data/adjusted/` as year-partitioned Parquet and in `data/duckdb/nse.duckdb`. The health report is written to `reports/data_health.html`. For daily updates, configure a current NSE bhavcopy URL or provide a local downloaded file. Weekdays are treated as sessions except dates in `config/data.yaml` `holidays`; update that list for exchange holidays and special sessions.

### Phase 2: event studies

```sh
uv run --no-sync nse-engine study run --setup pullback_uptrend --split train
uv run --no-sync nse-engine study sweep --setup pullback_uptrend --split train
uv run --no-sync nse-engine study report
```

Studies append every setup/parameter/split/horizon result to the SQLite registry at `data/duckdb/trials.sqlite` and save per-event Parquet files in `reports/study_events/`. The report applies Benjamini-Hochberg correction across logged trials and evaluates configured approval gates. Holdout data stays locked; accessing it requires `--unlock-holdout`, which adds an audit row to the registry. Rates in `config/costs.yaml` are illustrative and must be verified before relying on results.

### Phase 3: portfolio backtests

Run an approved setup portfolio on a fixed, non-holdout split:

```sh
uv run --no-sync nse-engine backtest run --split train
uv run --no-sync nse-engine backtest run --split validation
uv run --no-sync nse-engine backtest walk-forward --split train
uv run --no-sync nse-engine backtest walk-forward --split validation
```

Backtests read only configurations listed in config/approved_setups.yaml; generate and review that file with nse-engine study report first. Walk-forward runs use the declared setup parameter grids, choose the best Sharpe setup on each past-only training window, then test on the next window. Portfolio limits and rolling windows live in config/backtest.yaml. Each run writes a uniquely named folder under reports/ containing an HTML Plotly report and Parquet equity, order, trade, and (for walk-forward) window tables. Backtest experiments append to the same immutable SQLite trial registry.

## Project phases

0. Bootstrap: tooling, config, CLI, logging.
1. Data: adjusted daily data, storage, quality checks.
2. Event study: setups, statistics, trial registry, validation gates.
3. Portfolio backtest: simulator and walk-forward analysis.
4. Paper trading: scanner, simulated fills and ledger.
5. UI and reports: dashboard and charts.
6. Broker interface: simulated interfaces only; live orders remain disabled.

See [docs/SPEC.md](docs/SPEC.md) for requirements and acceptance criteria.
