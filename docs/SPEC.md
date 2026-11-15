# NSE Setup Research Engine: Technical Guide and Codex Prompt Pack

Version 1.0 · 15 Nov 2026 · Research software only. It does not guarantee profit and is not financial advice.

## 0. How to use this document

1. Create an empty git repository.
2. Copy Section 2 into the repo root as `AGENTS.md`.
3. Save this whole document as `docs/SPEC.md`.
4. Paste the phase prompts from Section 16 into Codex one at a time. Do not start a phase until the previous phase's acceptance criteria pass.
5. Review every diff. The agent writes code; you decide what is trusted.

## 1. Mission and scope

**Mission.** Build a local, zero-cost research engine that finds rule-based trading setups with a statistically validated, cost-adjusted edge on NSE equities, trades them only when they fire, and later (deferred) routes orders through the Groww API.

**Core principle.** Most ideas will fail. The engine exists to reject weak ideas cheaply and to prevent self-deception (overfitting, look-ahead bias, ignoring costs). Doing nothing on a day is a valid outcome.

**Non-goals for now.** No intraday or HFT, no options or futures, no machine-learning price prediction, no live order submission, no cloud deployment.

| Phase | Name | Output |
| --- | --- | --- |
| 0 | Bootstrap | Repo, tooling, CI, config system |
| 1 | Data | Clean, adjusted daily NSE data in Parquet and DuckDB, with health checks |
| 2 | Event study | Setup library, forward-return statistics, validation gates, trial registry |
| 3 | Portfolio backtest | Realistic daily simulator, walk-forward, reports |
| 4 | Paper trading | Daily scanner, order plans, simulated ledger, decay monitor |
| 5 | UI and reports | Streamlit dashboard, Plotly charts |
| 6 | Broker interface | Abstract interface, fake broker, Groww adapter skeleton. Live orders stay disabled |

## 2. AGENTS.md (copy verbatim into repo root)

```
# AGENTS.md
Project: NSE Setup Research Engine. Read docs/SPEC.md before any task.

## Working agreement
- Work only on the phase named in the prompt. Do not start later phases.
- Do not ask clarifying questions. State assumptions in docs/ASSUMPTIONS.md and continue.
- Before finishing: run `make check` (ruff, mypy, pytest) and fix all failures.
- Make small, logically separate commits with clear messages.
- Update README.md with how to run what you built.

## Hard rules
1. No look-ahead. A signal computed from data up to and including the close of day t may only create an order for day t+1 or later. Add a test for this on every new setup or indicator.
2. All costs, slippage, thresholds and dates come from YAML config. No magic numbers in code.
3. Never use eval or exec. Setups are Python classes registered in a registry, with parameters from YAML.
4. Never commit secrets. Credentials come from environment variables. Provide .env.example only.
5. Never place real orders. No code path may call a live order endpoint in any phase before Phase 6, and even then it must be disabled by default (DRY_RUN=true).
6. Every experiment (setup plus parameters plus split) is appended to the trial registry. No silent re-runs or deleted trials.
7. The holdout period is locked. Reading it requires an explicit flag and is logged.
8. Deterministic results: seed all randomness, pin dependency versions.
9. Prefer simple, readable code. Type hints everywhere. Docstrings on public functions.
10. Every report must print the disclaimer: research output, not advice, no guarantee of profit.
```

## 3. Domain constraints (India)

The engine must model these realities. Confirm every number in Section 17 before trusting it.

- **Sessions.** NSE cash market is open 09:15 to 15:30 IST, Monday to Friday except exchange holidays. Daily bhavcopy is typically published after close; schedule data pulls for the evening (about 17:30 IST) with retries.
- **Settlement.** T+1 for equities. Cash segment shorting is intraday only, so the daily engine is **long-only** with delivery product.
- **Circuits.** Stocks have price bands. Skip or flag symbols that hit circuit on the entry day; treat such fills as unreliable.
- **Corporate actions.** Splits, bonuses, rights and dividends distort raw prices. Keep raw and adjusted prices separate. Never compute returns on unadjusted series.
- **Survivorship bias.** Free datasets often omit delisted stocks. Record the bias in every report and prefer point-in-time index membership where available.
- **Costs.** Brokerage, STT, exchange transaction charges, SEBI fee, stamp duty, GST and DP charges apply. Rates change, so they live in config.
- **Order type.** Retail algo rules and some brokers restrict market orders. All simulated and future real orders are **limit orders**.
- **Regulation for later phases.** SEBI retail algo framework (mandatory from 1 Apr 2026): static IP whitelisting, exchange Algo-ID tagging, daily 2FA login, order-rate thresholds. Phase 6 only prepares for this.

## 4. Tech stack and conventions

- Python 3.11 or newer. Package manager: `uv` (fallback pip). Project layout via `pyproject.toml`, src layout.
- Libraries: pandas, numpy, pyarrow, duckdb, pydantic (config validation), pyyaml, typer (CLI), plotly, streamlit, scipy and statsmodels (statistics), requests or httpx (downloads), tenacity (retries), pytest, hypothesis, ruff, mypy.
- Storage: Parquet files under `data/` plus a DuckDB database for queries. SQLite for the trial registry and paper ledger.
- Time: store dates as naive `date` in IST market time. Store any timestamps in UTC with explicit conversion helpers.
- Logging: standard `logging`, JSON lines to `logs/`.
- `Makefile` targets: `setup`, `check`, `test`, `lint`, `data`, `study`, `backtest`, `paper`, `ui`.

## 5. Repository layout

```
nse-engine/
  AGENTS.md  README.md  Makefile  pyproject.toml  .env.example
  docs/ SPEC.md ASSUMPTIONS.md DECISIONS.md
  config/
    base.yaml costs.yaml universe.yaml splits.yaml
    setups/*.yaml
  src/nse_engine/
    cli.py
    config.py
    calendar.py
    data/ sources/ (eod2_import.py, nselib_source.py, base.py) store.py adjust.py quality.py universe.py
    features/ indicators.py registry.py
    setups/ base.py registry.py pullback_uptrend.py breakout_52w.py vol_contraction.py index_dip.py
    study/ events.py stats.py gates.py trials.py
    backtest/ engine.py fills.py portfolio.py costs.py metrics.py walkforward.py
    paper/ scanner.py ledger.py runner.py decay.py
    broker/ base.py fake.py paper_broker.py groww.py ratelimit.py
    reporting/ charts.py html_report.py
    ui/ app.py pages/
  tests/ unit/ integration/ fixtures/
  data/ raw/ adjusted/ duckdb/ (gitignored)
  reports/ logs/ (gitignored)
```

## 6. Data layer specification (Phase 1)

**Sources, behind a common `DataSource` interface:**

1. **EOD2 import (primary start point).** Import CSVs produced by the open-source EOD2 project, which provides daily OHLCV and delivery data for NSE stocks, already adjusted for splits and bonuses. Treat pre-2005 data as lower quality and start analysis at 2005 or later by config.
2. **NSE bhavcopy via nselib or direct download (secondary).** Raw, unadjusted. Used for daily updates and cross-checks. NSE changed to the UDiFF format in July 2024, so handle both formats.
3. **yfinance (optional).** Cross-check only, never a primary source.

Sources are unofficial scrapers and can break. The code must fail loudly, retry with backoff, and never overwrite good data with an empty download.

**Tables (Parquet, partitioned by year, also registered in DuckDB):**

`ohlcv_daily`: symbol, date, open, high, low, close, volume, close\_raw, delivery\_qty (nullable), delivery\_pct (nullable), source, ingested\_at.

`calendar`: date, is\_trading\_day, note.

`universe_membership`: symbol, index\_name, start\_date, end\_date (point-in-time where available, otherwise a static list flagged as biased).

`corporate_actions` (optional): symbol, ex\_date, action\_type, ratio.

**Quality checks (`nse-engine data check`), each producing a report row:**

- Missing trading days per symbol and gaps over N days.
- High below low, close outside the high-low range, zero or negative prices, zero volume on trading days.
- Single-day returns beyond a configurable threshold (likely unadjusted splits). List them for manual review.
- Adjusted versus raw consistency around known corporate-action dates.
- Duplicate rows, stale repeated closes.
- Symbol continuity and renames.
- Coverage per year (number of symbols), to expose survivorship bias.

Exit non-zero if critical checks fail. Write `reports/data_health.html`.

**CLI:** `nse-engine data import-eod2 --path`, `data update`, `data check`, `data summary`.

## 7. Indicators and setups

**Indicator functions** are pure, vectorised, per-symbol, and use only past and current-bar data: SMA, EMA, RSI (Wilder, with length param), ATR, rolling high and low, rolling volume and traded value averages, rate of change, percentile rank, Bollinger bands, volatility contraction ratio (short ATR over long ATR), distance from 52-week high, relative strength versus Nifty 50.

**Setup interface (`setups/base.py`):**

```python
class Setup(Protocol):
    name: str
    def required_features(self) -> list[str]: ...
    def signals(self, df: pd.DataFrame) -> pd.Series: ...  # bool per row, evaluated at close of day t
```

`config/setups/<name>.yaml` holds universe, params, filters (minimum price, minimum average traded value), and exit rules. Starter hypotheses to implement (each needs an economic rationale written in the YAML `rationale:` field):

- `pullback_uptrend`: close above SMA(200), RSI(2) below a threshold, liquidity filter.
- `breakout_52w`: close at a 52-week high with volume above its 20-day average.
- `vol_contraction`: short ATR over long ATR below a threshold, followed by a range breakout.
- `index_dip`: Nifty 50 ETF or index falls sharply over N days while above its long average.
- `delivery_spike` (optional): delivery percentage far above its rolling average with positive close.

A Nifty 50 index series is needed as benchmark and regime filter. Implement regime labels (index above or below SMA(200), volatility tercile) for baseline matching.

## 8. Event-study specification (Phase 2)

**Timing convention (strict).** Signal at close of day t. Entry on day t+1 using the limit-order fill model in Section 9. Exits by horizon (close of day t+h for h in a configured list such as 1, 3, 5, 10, 20) or by stop and target rules checked on daily high and low from day t+1.

**Ambiguity rule.** If a bar touches both stop and target, assume the stop hit first. If the open gaps through a stop, fill at the open.

**Per-event outputs:** symbol, signal date, entry date and price, exit date and price, gross return, net return after costs, MAE (max adverse excursion), MFE (max favourable excursion), holding days, regime labels.

**Baseline.** For each event date, the baseline is the average net return of all eligible stocks that day (same universe, same liquidity filter, same horizon), plus a regime-matched variant. Report excess over baseline.

**Statistics per setup, horizon and split:**

- n\_events and n\_independent\_dates (number of distinct signal dates).
- Mean, median, standard deviation, hit rate, average win, average loss, payoff ratio, expectancy.
- 1st and 5th percentile returns, worst loss.
- Excess over baseline with a **date-clustered t-statistic** (average per date first, then test across dates; add a Newey-West variant for overlapping horizons).
- Per-year table, and breadth (share of symbols with positive expectancy).
- Cost stress: results at 1.0x, 1.5x and 2.0x of configured costs and slippage.
- Bootstrap confidence interval of expectancy using block bootstrap by date.

**Splits (config):** train, validation and holdout date ranges, for example 2005 to 2017, 2018 to 2022, 2023 to latest. The holdout is read only with `--unlock-holdout`, which writes an entry to the trial registry.

**Trial registry (SQLite):** trial\_id, timestamp, setup name, params hash, universe, split, horizon, metrics JSON, git commit. The total number of trials is used for multiple-testing correction (Benjamini-Hochberg false discovery rate across all logged trials, plus a deflated Sharpe style adjustment for backtest-level results).

**CLI:** `nse-engine study run --setup pullback_uptrend --split train`, `study sweep` (grid over a small, declared parameter set, every point logged), `study report`.

## 9. Portfolio backtest specification (Phase 3)

Daily loop, event-driven, long-only, no leverage.

- **Order flow.** After close t: evaluate approved setups, rank candidates by a deterministic score, create limit orders for t+1.
- **Limit-order fill model.** Buy limit = signal close times (1 + buffer\_bps). If the next open is at or below the limit, fill at the open. Else if the next low is at or below the limit, fill at the limit. Else no fill. Add slippage bps on top. Skip fills on circuit-locked bars.
- **Sizing.** Fixed fractional risk: shares = capital times risk\_per\_trade\_pct divided by (stop distance, e.g. ATR times multiple). Whole shares only, cash constrained, max positions, max per-symbol and per-sector weight.
- **Exits.** Stop, target, time exit, and optional trailing rule. Exits also use limit or stop-limit style simulation with gap handling.
- **Costs.** Applied per fill from `config/costs.yaml` (Section 10).
- **Metrics.** CAGR, annualised volatility, Sharpe and Sortino (risk-free rate from config), maximum drawdown and duration, Calmar, exposure, turnover, trade count, win rate, payoff ratio, expectancy, profit factor, best and worst trade, comparison against Nifty 50 buy-and-hold.
- **Walk-forward.** Rolling train and test windows. Parameters are chosen on train only and evaluated on the following test window. Output the stitched out-of-sample equity curve.
- **Robustness.** Monte Carlo resampling of the trade sequence (drawdown distribution), parameter-neighbourhood stability table, and cost-stress runs.

## 10. Cost model

Implement `costs.py` as pure functions driven by `config/costs.yaml`. The values below are **illustrative placeholders. Verify against Groww's current charge sheet and NSE and SEBI notifications before trusting any result.**

```yaml
delivery_equity:
  brokerage_pct: 0.0        # verify with Groww
  brokerage_flat_per_order: 0.0
  stt_pct_buy: 0.10         # verify
  stt_pct_sell: 0.10        # verify
  exchange_txn_pct: 0.00297 # verify
  sebi_fee_pct: 0.0001      # verify
  stamp_duty_pct_buy: 0.015 # verify
  gst_pct: 18               # applies to brokerage, exchange charges, SEBI fee
  dp_charge_per_sell_scrip: 15.34  # verify
slippage_bps: 10
stress_multipliers: [1.0, 1.5, 2.0]
```

Include unit tests with hand-computed examples for one buy and one sell.

## 11. Validation gates (`study/gates.py`)

A setup is `approved` only if all gates pass. Thresholds come from config:

1. At least a configured minimum of independent signal dates in both train and validation.
2. Positive net expectancy at 2.0x cost stress on train, validation and holdout.
3. Excess over baseline significant after Benjamini-Hochberg correction across all logged trials.
4. Positive net result in at least 60 percent of calendar years.
5. Breadth: positive in at least 55 percent of symbols (or sectors).
6. No single year contributes more than 40 percent of total profit.
7. Parameter neighbours (plus or minus one step) also positive, to avoid knife-edge optima.
8. A written economic rationale exists in the setup YAML.

Output a pass or fail table with reasons. Approved setups are written to `config/approved_setups.yaml` with the validated metric distribution used later by the decay monitor.

## 12. Reporting and UI (Phase 5)

Streamlit multipage app plus standalone Plotly HTML reports. Pages: Data health, Setup explorer, Event-study results, Backtest, Walk-forward, Paper ledger, Trial registry.

Charts: candlestick with indicator overlays and event markers, equity curve versus benchmark, underwater drawdown, return distribution histogram, per-year bar chart, MAE and MFE scatter, parameter-stability heatmap (with a warning label that heatmaps invite overfitting), Monte Carlo drawdown fan.

Every page and report footer shows the disclaimer and the survivorship-bias status.

## 13. Paper trading (Phase 4)

- `nse-engine paper run [--date YYYY-MM-DD]` runs after the evening data update. It is idempotent per date.
- Steps: update data, run quality checks, evaluate approved setups, generate an order plan (JSON and markdown: symbol, limit price, quantity, stop, target, reason), simulate fills the next trading day from that day's OHLC using the Section 9 fill model, update the ledger.
- SQLite tables: runs, signals, orders, fills, positions, ledger.
- Daily markdown report with open positions, realised and unrealised PnL, costs paid, and a comparison against the validated metric band.
- **Decay monitor:** if a setup's live drawdown or hit rate leaves the validated band for a configured number of trades, mark it `suspended` and stop generating orders for it.
- If no setup fires, write a report saying so. No trades is a normal result.

## 14. Broker interface and Groww (Phase 6, live deferred)

Build the interface now so strategy code never imports a broker.

```python
class Broker(Protocol):
    def place_limit_order(self, symbol, side, qty, price, client_ref) -> OrderAck: ...
    def cancel_order(self, order_id) -> None: ...
    def get_order(self, order_id) -> Order: ...
    def positions(self) -> list[Position]: ...
    def holdings(self) -> list[Holding]: ...
    def ltp(self, symbols) -> dict[str, float]: ...
```

Implementations: `FakeBroker` (tests), `PaperBroker` (ledger), `GrowwBroker` (skeleton only, with every order method raising `LiveTradingDisabled` unless `DRY_RUN=false` **and** `LIVE_CONFIRM` is set, and even then not implemented until the owner asks).

Facts to design against (verify against Groww's current docs): REST base `https://api.groww.in` with an official Python SDK; auth via API key and TOTP; **access token expires daily at about 06:00 IST**; order placement requires a registered **static IP**; market-data endpoints need a paid Live Data subscription; indicative limits of 10 orders per second and 250 per minute, and 10 data requests per second and 300 per minute; batch LTP and OHLC up to 50 symbols per call; historical candle intervals from 1 minute to 1 month. Build a token-bucket rate limiter, idempotent client references, retry with backoff, position reconciliation against the broker, a global kill switch, a daily loss cap and a max-order-size guard.

## 15. Testing and acceptance

Global requirements: `make check` passes, coverage above 80 percent on core modules, deterministic outputs.

Mandatory tests:

- **Look-ahead test:** for every indicator and setup, truncate data at day t and confirm signal at t is unchanged when later data is removed or altered.
- **Fill-model tests:** gap through stop, both stop and target in one bar, limit not reached, circuit-locked bar.
- **Cost tests:** hand-computed buy and sell examples.
- **Statistics tests:** clustered t-stat versus a known synthetic case, BH correction against a reference implementation.
- **Synthetic edge test:** generate random-walk data with an injected known edge and check the study detects it; on pure noise check that gates reject at the expected rate.
- **Holdout lock test:** reading holdout without the flag raises.
- **Idempotency test:** running paper for the same date twice does not duplicate rows.

## 16. Phase prompts for Codex

Paste one at a time. Each assumes AGENTS.md and docs/SPEC.md are in the repo.

**Prompt 0: Bootstrap**

```
Read AGENTS.md and docs/SPEC.md. Implement Phase 0 only: pyproject.toml (src layout, pinned deps from SPEC section 4), Makefile targets, ruff and mypy config, pytest setup, pydantic-based config loader for config/*.yaml, Typer CLI skeleton `nse-engine`, logging setup, .gitignore, .env.example, and the folder structure in SPEC section 5 with empty modules and docstrings. Add a smoke test. Acceptance: `make check` passes on a clean checkout.
```

**Prompt 1: Data**

```
Implement Phase 1 per SPEC sections 3, 6. Build the DataSource interface, the EOD2 CSV importer, an nselib/bhavcopy updater handling both legacy and UDiFF formats, Parquet and DuckDB storage, the calendar, the universe loader, and all quality checks with an HTML data_health report. Use fixtures in tests/fixtures; do not require network in tests. Mock downloads, add retries with backoff, and never overwrite good data with empty downloads. Acceptance: SPEC section 6 checks run on fixture data, CLI commands work, make check passes.
```

**Prompt 2: Event study**

```
Implement Phase 2 per SPEC sections 7, 8, 10, 11. Build indicators, the Setup protocol and registry, the four starter setups with YAML configs, the event builder with the strict timing convention and fill model, statistics (clustered t-stats, bootstrap, cost stress, BH correction), split handling with the locked holdout, the SQLite trial registry, validation gates, and CLI commands. Add every mandatory test in SPEC section 15 that applies. Acceptance: a synthetic-edge integration test passes, noise is rejected at the expected rate, and a report is generated for a sample setup.
```

**Prompt 3: Backtest**

```
Implement Phase 3 per SPEC sections 9 and 10. Build the daily portfolio simulator, limit-order fill model, sizing, exits, cost application, metrics, walk-forward, Monte Carlo and cost-stress runs, and HTML reports with Plotly charts. Reuse the Phase 2 fill logic; do not duplicate it. Acceptance: fill-model and cost tests pass, a walk-forward run on fixture data completes deterministically, and results reconcile with a hand-checked mini example.
```

**Prompt 4: Paper trading**

```
Implement Phase 4 per SPEC section 13. Build the scanner, order-plan generator, simulated next-day fills, SQLite ledger, daily markdown report, decay monitor and the idempotent `paper run` command. Acceptance: running twice for the same date yields identical state, the no-signal day report is produced, and a decay-suspension test passes.
```

**Prompt 5: UI**

```
Implement Phase 5 per SPEC section 12: Streamlit multipage app and the chart library. Read from existing stores only; no new business logic in the UI layer. Acceptance: the app starts with `make ui`, every page renders on fixture data, and the disclaimer and survivorship status appear on every page.
```

**Prompt 6: Broker interface (live disabled)**

```
Implement Phase 6 per SPEC section 14 with live trading DISABLED. Build the Broker protocol, FakeBroker, PaperBroker, a token-bucket rate limiter, the kill switch and risk guards, and a GrowwBroker skeleton whose order methods raise LiveTradingDisabled. Do not implement any real order submission. Add tests proving that no live call path is reachable by default. Acceptance: make check passes and the safety tests pass.
```

## 17. Items to verify manually before relying on results

- Current Groww charges, API pricing, rate limits, order-type rules and static IP procedure.
- Current STT, exchange, SEBI, stamp duty, GST and DP charge rates.
- SEBI and NSE retail algo requirements, including market-order policy and any order-rate thresholds, before any live use.
- EOD2 and nselib data completeness: spot-check adjusted prices against known splits and bonuses, and check delisted-stock coverage.
- Data-source terms of use.
- NSE holiday calendar for each year.
- Tax treatment of your trading (consult a chartered accountant).

## 18. Definition of done for the research stage

The research stage is finished when you can show, from your own machine: clean adjusted data with a passing health report; at least a handful of setups tested with every trial logged; a clear approved or rejected verdict for each against the locked holdout; and, for any approved setup, a paper-trading record of at least one to three months consistent with its validated metrics. Only then consider the paid live setup (Groww API subscription and a static IP), and begin with minimum size and hard loss limits.
