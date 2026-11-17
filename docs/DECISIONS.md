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

# Phase 5 decision (2026-11-18)

- The dashboard is a read-only view over existing artifacts. It does not rerun studies, create orders, write approvals, append trials, or unlock the holdout.
- Every page renders the configured data-bias status and the research disclaimer, and the sidebar caption states that the holdout is locked and live orders are disabled. This makes a survivor-biased chart hard to mistake for tradable evidence.
- The invalid look-ahead positive control is tagged from its event filename and excluded from event charts and trial tables by default. It remains in its isolated registry for plumbing evidence only.
- Chart builders are pure functions and standalone HTML reports repeat the disclaimer and bias banner. Display bounds live in `config/ui.yaml`; research and cost thresholds stay in their existing YAML files.

# Phase 6 decision (2026-11-18)

- The broker layer is interface-first and live-off: strategy code depends only on the `Broker` protocol and value types in `nse_engine.broker.base`, never a concrete adapter.
- All broker limits (dry-run flag, rate bursts/refills, batch size, kill switch, daily loss cap, max order size, max positions, and credential environment-variable names) live in `config/broker.yaml`. No threshold is hard-coded in an adapter.
- `GrowwBroker` is a skeleton with no network calls. Every order, query, and price method raises `LiveTradingDisabled` unconditionally; even `DRY_RUN=false` plus `LIVE_CONFIRM` cannot enable a live call path. Real order placement remains a separate, explicitly authorised change.
- `FakeBroker` and `PaperBroker` satisfy the same protocol for tests and ledger-backed simulation, and `PaperBroker.reconcile()` compares simulated positions with the durable ledger. No live endpoint is reachable from either.

# Factor-study pre-registration decision (2026-11-18)

- "Go next" was interpreted as authorization to freeze the factor-study design before any data access, not as authorization to buy data or write execution code.
- Owner slots were filled with conservative defaults: value as trailing earnings yield, quality as ROE, momentum as 12-1 month return, and their equal-weight z-score composite; top-200 delisted-inclusive universe; top quintile; monthly rebalance; 50% one-way turnover cap; one-session signal-to-order lag; one-session fundamental announcement lag; and a ₹0 tuition budget carried forward from the owner's earlier answer.
- Every factor threshold is in `config/factor.yaml`; shared gates and costs remain in `config/gates.yaml` and `config/costs.yaml`. A guard test rejects `[OWNER DECISION]` placeholders and enforces positive forward lags and PIT requirements.
- No point-in-time data or announcement-dated fundamentals exist in the repo, so no study may run and no vendor may be contacted. Any post-data change to a frozen slot invalidates the study and requires a new pre-registration.

# Factor-study readiness decision (2026-11-18)

- The next bounded step is a readiness gate, not an execution engine. It verifies the frozen design hash, the presence of PIT inputs, causal lags, delisted-inclusive requirements, and locked holdout state, then refuses to proceed with a non-zero exit when any condition fails.
- `config/factor_data.yaml` names the PIT price, announcement, and membership paths, but those files do not exist yet. `make factor` therefore blocks by design; filling them is a separate owner data action and must not be done with survivor-biased EOD2 files.
- The readiness check is read-only: it does not compute factors, append trials, write approvals, unlock holdout, or contact a broker or vendor. The pre-registration hash guards against post-data edits to the frozen design.

# Public PIT acquisition decision (2026-11-18)

- "Let achieve this by script or bot in this solution" is interpreted as authorization to add local acquisition commands, not as authorization to run them, buy data, or execute a factor study. Filing-field extraction was deferred to a later bounded step.
- The bot is limited to public NSE endpoints with no credentials, vendor contact, or live order path. Every URL, date bound, weekday, delay, retry, directory, and exclusion is in `config/factor_data.yaml`; the frozen design and ₹0 budget are unchanged.
- Dated bhavcopies are immutable. Membership snapshots use only data available on or before each rebalance date, with a regression test proving later liquidity spikes cannot change earlier snapshots.
- Announcement metadata is stored at `announcement_index.parquet`, separate from the `fundamentals_path` output. Filing paths derive from a SHA-256 URL digest so repeated downloads are deterministic.
- NIFTYBEES stays in the price store for benchmark comparisons but is excluded from equity universe membership by an explicit configured symbol list; the regex exclusions continue to remove other ETFs and index series.
- Network acquisition is an explicit owner action. Verification uses mocked HTTP transports and synthetic local files only; no public endpoint was contacted during `make check`.

# Fundamentals parser decision (2026-11-18)

- The next bounded step adds a best-effort PDF text parser, not a factor study and not a trust gate. Extracted values require manual review before they are treated as evidence.
- Only the three frozen fields are recognized. Label patterns, the crore multiplier, and the label/value gap are in `config/factor_data.yaml`; changing them does not alter `config/factor.yaml` or the frozen hash.
- Parsed rows are append-only and announcement-dated. Reruns skip exact existing rows; restatements append later-dated rows. Missing or unparseable PDFs are reported rather than silently dropped.
- `select_fundamentals_as_of` enforces cutoff-based causality, with a test showing a 2018-style restatement pattern cannot alter a 2017 cutoff. The caller must still apply the configured session-based announcement lag.
- `pypdf==7.1.0` is pinned in `pyproject.toml` and `uv.lock`. No new credential, vendor, live-order, or network path is introduced by parsing.

# Public PIT smoke-test decision (2026-11-18)

- "Go next" after the blocked real run is interpreted as authorization for a bounded smoke test of the configured public acquisition path, not a full historical download or a factor run.
- A one-session 2026-11-13 UDiFF bhavcopy downloaded successfully from the configured public archive. Parsing exposed a real header mismatch: current files use `TTL_TRD_QNTY`, which the canonical volume aliases did not include.
- The parser now maps that alias, and a fixture using the live header proves volume, delivery quantity, delivery percentage, EQ-series filtering, and date parsing. The frozen factor design, readiness checks, and cost/gate thresholds were not changed.
- The one downloaded file remains under local `data/pit`, now gitignored. Full acquisition still requires a separate explicit action, followed by manifest/coverage review and manual fundamental validation before any real factor study.

# Factor-study execution decision (2026-11-18)

- The next bounded step builds the execution engine and candidate-only report. It does not acquire data, run a real study, approve a setup, or open the holdout.
- `factor run` rechecks readiness, then computes H1–H4 at the frozen horizons with causal fundamentals, point-in-time prices, winsorized z-scores, top-quintile baskets, turnover control, configured fill lag, and configured costs at the locked stress floor.
- Multiple-testing correction is per split because the splits are sequential and the holdout must remain independent. Rebalances whose full horizon would cross a split boundary are omitted rather than evaluated on truncated returns.
- All results are appended to the trial registry. Passing every gate is a candidate awaiting point-in-time verification, never an automatic approval; no code path writes `approved_setups.yaml` or changes `data_bias_status`.
- The HTML report repeats the candidate-only banner and disclaimer, shows all frozen hash and gate details, and includes calendar-year and benchmark comparisons. `make factor-run` is the operator entry point; it still blocks on the missing PIT inputs.
