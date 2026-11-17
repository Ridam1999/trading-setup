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

`data import-eod2` accepts either one CSV or a directory of per-symbol CSV files. For EOD2's per-symbol files, the importer uses each filename as the symbol when no ticker column is present. `data summary` reports whether the configured benchmark (NIFTYBEES by default) was imported. Real EOD2 daily CSVs use the `DLV_QTY` header and no delivery-percentage column; the importer maps `DLV_QTY` to `delivery_qty` and derives `delivery_pct` from the same row's volume. Delivery coverage starts in late 2019 and reaches ~96–97% from 2020 onward.

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

`delivery_spike` uses the delivery fields recovered from EOD2's `DLV_QTY` column; because delivery data begins only in late 2019, the locked train split (ending 2017) has no events and the setup cannot pass the two-split gates. `relative_strength_regime` compares a stock's trailing return with the configured benchmark's and requires a benign benchmark regime; the benchmark close is injected by the event builder as `_benchmark_close`.

Provisional mode is train/validation only: it rejects `--split holdout`, screens stock setups with the top-100 point-in-time liquidity universe in `config/universe_provisional.yaml`, and tags its registry rows so the report uses only those trials. Gates return `provisional_pass` or `rejected`, never `approved`; `approved_setups.yaml` is not written. The report is `reports/study_report_provisional.html` with a prominent `SURVIVOR_BIASED` banner. A failed setup is discarded; a pass is only a candidate for later verification.

Studies append every setup/parameter/split/horizon result to the SQLite registry at `data/duckdb/trials.sqlite` and save per-event Parquet files in `reports/study_events/`. The report applies Benjamini-Hochberg correction across logged trials and evaluates configured approval gates. `random_signal` is a seeded negative control with no price dependence; it must fail the gates. Holdout data stays locked; accessing it requires `--unlock-holdout`, which adds an audit row to the registry. With `research.data_bias_status: SURVIVOR_BIASED`, a statistical pass is provisional only and cannot populate approved_setups.yaml.

#### Real-data positive control (wiring check)

To prove that the real data, point-in-time universe, fills, costs, and statistics actually detect a known edge, run the deliberately invalid look-ahead control:

```sh
uv run --no-sync nse-engine study positive-control
```

The control signals when tomorrow's close is higher than today's close, so it is guaranteed to leak. It passed every gate at all five horizons (Benjamini–Hochberg p values from 0 to ~6.8e-110). A pass is **plumbing evidence only, never research evidence**. The control lives outside `SETUP_REGISTRY`, is defined in `config/controls/__control_lookahead__.yaml`, runs on train/validation only, and writes to a separate append-only registry at `data/duckdb/controls.sqlite`. Its rows are tagged `control: true` and are filtered out of both study reports and multiple-testing totals, so they can never produce a `provisional_pass` or enter `approved_setups.yaml`.

#### Free-data close-out

The free-data research stage is closed with **no approved setup**: all eight declared hypotheses were rejected across 100 setup/parameter/horizon gate groups on train and validation with the top-100 point-in-time liquidity universe and 2x cost stress. The research registry holds 280 append-only trials and the isolated control registry holds 10. Holdout remains locked, `config/approved_setups.yaml` stays `approved_setups: []`, and `config/research.yaml` stays `data_bias_status: SURVIVOR_BIASED`.

This is a verdict on **generic daily price and volume rules on survivor-biased data**. It does not prove that no edge exists elsewhere (for example, fundamental or cross-sectional factors, intraday data, options, or point-in-time/delisted-inclusive/total-return datasets). Because nothing survived, no dataset purchase or build is justified. See [docs/DECISIONS.md](docs/DECISIONS.md) for the full decision. Research output, not advice; no guarantee of profit.

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

### Phase 5: UI and reports

Launch the local dashboard with:

```bash
make ui
# equivalent: uv run --no-sync streamlit run src/nse_engine/ui/app.py
```

The Streamlit app has seven read-only pages: Data health, Setup explorer, Event-study results, Backtest, Walk-forward, Paper ledger, and Trial registry. Every page shows the configured data-bias status (`SURVIVOR_BIASED` in this repo), a locked-holdout/no-live-orders caption, and the required disclaimer. The sidebar selects the page; no page creates signals, orders, approvals, or trials.

Charts are built in `nse_engine.reporting.charts` and are also reusable from standalone HTML via `write_chart_report` in `nse_engine.reporting.html_report`. Chart/report helpers are pure functions: they format existing artifacts and never recompute research statistics. The UI reads only existing stores — `reports/data_health.csv`, adjusted OHLCV Parquet, saved event-study Parquet, immutable `reports/backtest_*` folders, `data/duckdb/paper.sqlite`, and the append-only trial registry. The invalid look-ahead positive control is excluded from event charts and trial tables by default. Display limits (default symbol, bar range, table rows, and event sample size) live in `config/ui.yaml`; gate, cost, and research thresholds remain in their own YAML files.

### Phase 6: broker interface (live disabled)

The broker layer is a set of Python types, not a CLI workflow:

```python
from pathlib import Path

from nse_engine.broker.config import load_broker_config
from nse_engine.broker.fake import FakeBroker
from nse_engine.broker.groww import GrowwBroker
from nse_engine.broker.paper_broker import PaperBroker
from nse_engine.paper.ledger import PaperLedger

config = load_broker_config()                              # config/broker.yaml
fake = FakeBroker(config)                                  # deterministic, in-memory
paper = PaperBroker(config, PaperLedger(Path("data/duckdb/paper.sqlite")))
groww = GrowwBroker(config)                                # every method raises LiveTradingDisabled
```

`Broker`, `OrderAck`, `Order`, `Position`, `Holding` and `Side` live in `nse_engine.broker.base`. Strategy code imports only those types, never a concrete adapter. `FakeBroker` applies the configured kill switch, daily loss cap, max-order-size, max-position, and client-ref idempotency guards, with deterministic simulated order IDs. `PaperBroker` places simulated orders through `FakeBroker` and sources positions/holdings from the durable paper ledger; `reconcile()` reports mismatches between the two. A token-bucket limiter in `nse_engine.broker.ratelimit` uses the order/data rates in `config/broker.yaml`.

`GrowwBroker` is a **disabled skeleton**: it opens no network connection, and every order, query, and price method raises `LiveTradingDisabled` even if `DRY_RUN=false` and the `LIVE_CONFIRM` environment variable are set. No real order submission exists. Credential variable **names** are configured in `config/broker.yaml` and documented in `.env.example`; no credentials are stored in the repo. Setting the live flags cannot enable the skeleton.

### Next research program: factor-study pre-registration

All implementation phases are complete. The next research design is frozen in [docs/FACTOR_STUDY_PREREG.md](docs/FACTOR_STUDY_PREREG.md) and [config/factor.yaml](config/factor.yaml): four cross-sectional hypotheses (value, quality, 12-1 momentum, and a composite), a point-in-time delisted-inclusive top-200 universe, top-quintile equal-weight monthly rebalancing with one-session signal-to-order lag, announcement-dated fundamentals with a restatement-leak fixture requirement, the existing strict gates with 2.0x cost stress, and a ₹0 tuition budget.

A readiness gate is available without running a study:

```bash
make factor
# equivalent: uv run --no-sync nse-engine factor check
```

It validates `config/factor.yaml`, verifies that `docs/FACTOR_STUDY_PREREG.md` still matches its configured SHA-256 hash, checks the point-in-time input paths in `config/factor_data.yaml`, confirms causal lags and PIT requirements, and checks that the registry still has no holdout trials or unlocks. Until the acquisition commands below create those inputs, it exits non-zero and lists the missing paths. It computes no factors, writes no trials, places no orders, and reads no holdout data.

### Public PIT acquisition bot

`config/factor_data.yaml` also holds the public NSE URL templates, date bounds, weekday filter, delays, retries, symbol exclusions, and filing text-extraction rules used by a local-only acquisition bot. It uses no credentials, contacts no vendor, and has no live order path. The frozen factor design and ₹0 budget are unchanged. PDF text extraction uses the pinned `pypdf==7.1.0` dependency.

```bash
make factor-acquire
# equivalent: uv run --no-sync nse-engine factor acquire --help

uv run --no-sync nse-engine factor acquire prices
uv run --no-sync nse-engine factor acquire membership
uv run --no-sync nse-engine factor acquire announcements
uv run --no-sync nse-engine factor acquire filings
uv run --no-sync nse-engine factor acquire fundamentals
```

Run them in that order. `prices` writes immutable dated bhavcopies under `data/pit/bhavcopy`; `membership` derives top-N snapshots using only data available at each rebalance date; `announcements` downloads public announcement metadata into `data/pit/announcement_index.parquet` and raw JSON chunks; `filings` downloads linked PDFs without overwriting existing copies; `fundamentals` appends parsed, announcement-dated EPS, net-profit, and equity rows to `data/pit/fundamentals_announcements.parquet`. `filings` and `fundamentals` apply the headline patterns and URL suffixes in `config/factor_data.yaml`, so unrelated NSE announcements are not downloaded or parsed. NIFTYBEES remains in the price store as the benchmark reference but is excluded from equity membership by `config/factor_data.yaml`. All commands print the research disclaimer.

A bounded live smoke test downloaded the public 2026-11-13 UDiFF bhavcopy and parsed 2,744 EQ rows including NIFTYBEES. It exposed a current-header volume alias (`TTL_TRD_QNTY`), which is now mapped and covered by a fixture. The downloaded file stays under local `data/pit` and is gitignored; full historical acquisition has not been run.

The parser is best-effort: label patterns and crore scaling live in YAML, missing or unparseable PDFs are reported, and prior rows are never overwritten. Malformed PDFs are counted as unparsed instead of aborting the batch. A restatement appends a later-announcement row, and `select_fundamentals_as_of` shows that later values cannot leak into earlier cutoffs. Manual review is still required before these values are trusted. A bounded 2005–2022 public acquisition and a 200-filing diagnostic sample are summarized in [docs/FACTOR_DATA_REVIEW.md](docs/FACTOR_DATA_REVIEW.md); extraction coverage was insufficient, so the real factor run remains blocked pending a manual data-source decision.

### Frozen factor-study execution

The execution layer is available but remains blocked until trusted fundamentals exist (historical prices and PIT membership are present; the diagnostic fundamentals sample is not approved input):

```bash
make factor-run
# equivalent: uv run --no-sync nse-engine factor run
# explicit, audited holdout access:
uv run --no-sync nse-engine factor run --unlock-holdout
```

`factor run` first rechecks the frozen hash, PIT inputs, causal lags, locked holdout state, and cost-stress floor. It then evaluates H1–H4 at the four frozen 1/3/6/12-month horizons on train and validation only unless `--unlock-holdout` is passed. Signals use announcement-dated fundamentals after the configured lag, point-in-time closes, winsorized z-scores, the top-quintile basket, the configured turnover cap, and the shared next-session limit-fill model with configured costs. Rebalance dates whose full horizon would cross a split boundary are dropped, so a default run cannot read holdout prices. Benjamini–Hochberg correction is applied separately within each split across the 4 × 4 hypothesis/horizon combinations.

Every hypothesis/horizon/split result is appended to the append-only trial registry. A pass is recorded only as a candidate requiring point-in-time verification; the command never writes `config/approved_setups.yaml`, never changes `data_bias_status`, and never places an order. It writes `reports/factor_study.html` (configurable in `config/factor_data.yaml`) with the bias banner, frozen hash, gate table, calendar-year results, benchmark comparisons, and the required research disclaimer.

`make check` includes mocked HTTP tests, synthetic dated bhavcopies, a causality test showing that a later liquidity spike cannot change an earlier membership snapshot, and restatement/idempotency tests for fundamentals parsing. It also includes guard tests for owner-decision placeholders, positive signal/announcement lags, blocked/ready readiness paths, six synthetic factor-study tests (default train/validation-only execution, causality under later price spikes and restatements, audited holdout access, blocked CLI execution, candidate-only reports, and rejection of a lowered cost-stress floor), and approval-file safety. No network acquisition, real factor run, vendor contact, approval, or holdout access occurred during verification. Changing any frozen slot after data access invalidates the study and requires a new pre-registration.

## Project phases

0. Bootstrap: complete.
1. Data: EOD2 market history loaded with partitioned health checks; PIT import, corporate-action adjustment, and verification commands added. Source remains survivor-biased until licensed PIT data is supplied and reviewed.
2. Event study: free-data research closed out; all eight setups (ETF and stock, plus a seeded random-signal control) rejected across 100 gate groups, with 0 provisional passes and 0 approvals. An isolated cheating positive control validated real-data wiring. Holdout remains locked and `SURVIVOR_BIASED` is unchanged.
3. Portfolio backtest: implementation complete; synthetic acceptance passes, real-data run pending.
4. Paper trading: implementation complete; the optimized real-data no-signal run is verified, and approvals remain empty.
5. UI and reports: complete with a seven-page read-only Streamlit dashboard and reusable Plotly/HTML charts; `make ui` launches it.
6. Broker interface: complete with live trading disabled; Groww order methods always raise `LiveTradingDisabled`.
7. Next research program: factor study pre-registered and frozen with a ₹0 budget; the readiness gate, public-data acquisition bot, fundamentals parser, execution engine, and candidate-only report exist. Public prices, membership, and announcements were acquired only through 2022, but extracted fundamentals failed diagnostic review. No real factor study has run, no trials have been appended, and no holdout data has been accessed.

See [docs/STATUS.md](docs/STATUS.md) for verification and manual prerequisites, [docs/REAL_DATA_RUNBOOK.md](docs/REAL_DATA_RUNBOOK.md) for the adapted EOD2 workflow, and [docs/SPEC.md](docs/SPEC.md) for requirements and acceptance criteria.
