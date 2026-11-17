# Decisions

## Phase 0

- Use a `src/` package layout and expose the CLI as `nse-engine`.
- Load configuration from YAML files in `config/`, validating the shared base settings with Pydantic and retaining other YAML documents as typed mappings for later phase schemas.
- Keep generated data, reports, logs, local environments, and secrets out of version control.
- Phase 2 setup evaluation uses explicit class registration, causal per-symbol indicators, next-session limit entry, stop-first daily-bar ambiguity resolution, date-clustered inference, and an auditable holdout unlock event.
# Phase 3 decisions

- Shared limit-entry, stop/target, gap, circuit-lock, and time-exit functions live in backtest/fills.py; Phase 2 event studies now call the same helpers.
- Portfolio reports bundle an embedded Plotly HTML chart, with immutable, timestamped Parquet outputs for equity, trades, orders, and walk-forward windows.
- Portfolio backtest registry rows use a backtest: split prefix and experiment_type metric tag. Event-study reporting excludes those portfolio rows from its event p-value correction and setup gates.
- Walk-forward selection uses training-window Sharpe over the finite, declared YAML candidate set. It never reads validation or holdout results to select candidates.

# Phase 4 decisions

- Paper state lives in a dedicated SQLite ledger with a primary key on run date. One transaction covers each run so signals, orders, fills, positions, cash, and the run record commit together; a repeated date returns the stored result.
- Scanner and fill logic reuse the Phase 2/3 setup registry, liquidity screens, and shared limit/stop/time-exit helpers. No new order-routing or broker code was added.
- Each run writes a deterministic markdown report and JSON order plan under `reports/paper/`. Both include the research disclaimer; the report also states the configured data-bias status.
- Suspended setups are excluded in the scanner, so a decay suspension immediately stops new order generation while existing positions continue to exit normally.

# Free-data research close-out (2026-11-18)

- **Decision:** close the free-data research stage with no approved setup. All eight declared hypotheses — ETF timing, pullback, 52-week breakout, volatility contraction, index dip, delivery spike, relative-strength/regime, and the seeded `random_signal` negative control — were rejected across 100 setup/parameter/horizon gate groups on locked train and validation splits with the top-100 point-in-time liquidity universe and 2x cost stress. There are zero `provisional_pass` verdicts and zero approvals.
- **Verdict scope:** this is a verdict on **generic daily price and volume rules on survivor-biased free data**. It does not prove that no edge exists, and it says nothing about fundamental/cross-sectional factors, intraday data, options, or point-in-time/delisted-inclusive/total-return datasets. Failing on data whose survivorship bias flatters long-only rules is stronger evidence than the raw counts suggest, but it is still a kill stage, not a universal claim.
- **No dataset spend:** because nothing survived, buying a Groww/PIT subscription or building a static-IP data pipeline is not justified. Revisit only as a deliberate policy change to test a different hypothesis class, not as a way to rescue these rejected rules.
- **Data status:** `config/research.yaml` stays `data_bias_status: SURVIVOR_BIASED`. Holdout remains locked: zero unlock audit rows and zero holdout trials. `config/approved_setups.yaml` stays `approved_setups: []`. The close-out itself changed no phase state; Phases 5 and 6 were later implemented as tooling with live orders still disabled, so no live-order path exists.
- **Delivery investigation:** the null delivery fields were an importer bug. Real EOD2 daily CSVs use `DLV_QTY` with no percentage column; the importer now maps that alias and derives `delivery_pct` from the same row's volume. After a full re-import, delivery values cover 48.21% of rows, starting in late 2019 and reaching ~96–97% from 2020. The locked train split ends in 2017, so `delivery_spike` still cannot pass the two-split gates; its verdict remains a coverage rejection, not a verdict on accumulation. Earlier all-null trials remain in the append-only registry.
- **Pipeline validation:** a deliberately invalid `study positive-control` setup signals when tomorrow's close is higher than today's close. On the same real data, PIT universe, fills, costs, and gates, it cleared every gate at all five horizons with Benjamini–Hochberg p values from 0 to 6.8e-110. It lives outside `SETUP_REGISTRY`, writes to a separate `data/duckdb/controls.sqlite` registry, is tagged `control: true`, is filtered out of both study reports and FDR totals, and can never enter `approved_setups.yaml`. A pass is plumbing evidence only.
- **Registry:** the research registry holds 280 append-only trials; the isolated control registry holds 10. No rows were updated or deleted.
- Research output, not advice; no guarantee of profit.

# Phase 6 decision (2026-11-18)

- The broker layer is interface-first and live-off: strategy code depends only on the `Broker` protocol and value types in `nse_engine.broker.base`, never a concrete adapter.
- All broker limits (dry-run flag, rate bursts/refills, batch size, kill switch, daily loss cap, max order size, max positions, and credential environment-variable names) live in `config/broker.yaml`. No threshold is hard-coded in an adapter.
- `GrowwBroker` is a skeleton with no network calls. Every order, query, and price method raises `LiveTradingDisabled` unconditionally; even `DRY_RUN=false` plus `LIVE_CONFIRM` cannot enable a live call path. Real order placement remains a separate, explicitly authorised change.
- `FakeBroker` and `PaperBroker` satisfy the same protocol for tests and ledger-backed simulation, and `PaperBroker.reconcile()` compares simulated positions with the durable ledger. No live endpoint is reachable from either.
