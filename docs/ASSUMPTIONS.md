# Assumptions

- The supplied technical guide is the authoritative project specification and is copied verbatim to `docs/SPEC.md`.
- At the initial Phase 0 inspection, the workspace had not yet been initialized as a Git repository and `uv` was unavailable. The repository and isolated project environment were subsequently created.
- Dependency versions in `pyproject.toml` are pinned. Phase 1 NumPy is pinned to 2.4.6 because the newer 2.5 line requires Python 3.12 while the specification supports Python 3.11.
- Phase 0 supplies a minimal Pydantic YAML config loader, Typer CLI, and logging setup. It does not implement Phase 1+ behavior.
- Phase 1 treats EOD2 close data as already adjusted and records NSE bhavcopy close in `close_raw`. The calendar models Monday-Friday sessions and relies on the configured holiday list; exceptional weekend sessions must be added explicitly in a later calendar enhancement.
- Universe membership is static unless point-in-time dates are supplied. The default empty symbol list is marked survivorship-biased.
- Phase 2 uses the adjusted `close` series for indicator, entry, and exit calculations; `close_raw` is retained only for comparison. The event-study baseline uses same-date, same-liquidity-filter eligible rows, and its regime-matched version pools eligible rows with the same benchmark trend/volatility labels.
- Setup sweep grids are finite YAML-declared values. A setup remains unapproved until the required neighbor values and train, validation, and explicitly unlocked holdout trials exist in the append-only registry.
# Phase 3 portfolio assumptions

- Walk-forward candidates and parameter values come only from the checked-in setup YAML files and their declared sweep grids. Each training window selects the highest in-sample Sharpe candidate; ties resolve by setup name and parameter order. The selector sees training rows only.
- Portfolio orders are ranked by prior-close traded value (close times volume), descending, with setup name and symbol as stable tie-breaks. Positions are sized to the smaller of fixed fractional stop risk, configured per-symbol weight, configured per-sector weight, and available cash.
- ATR times the configured multiple defines the stop distance when a positive ATR can be computed from pre-signal bars. The setup's configured stop percentage is the fallback. Targets use the setup target or the configured default.
- Stop checks precede target checks. A gap through the stop exits at the open. Circuit-locked bars cannot fill entries or exits; any remaining position is marked to market until an executable bar or the next available time exit.
- Open positions are closed at the final available bar when it is executable. If the final bar is circuit locked, the portfolio curve marks the position at close and the trade remains open.
- Commission and slippage stress runs replay the full portfolio with both configured order charges and slippage scaled by the same multiplier. Illustrative charge rates remain unverified.
- Monte Carlo resamples completed trade returns independently with replacement. It is a simple trade-sequence sensitivity summary and does not model changing market regimes or overlapping portfolio exposure.
- A static sector map may be added to config/backtest.yaml. Symbols without a sector assignment are capped individually rather than grouped into one synthetic sector.
- The backtest CLI permits train and validation only. Holdout remains inaccessible through the portfolio command.

# Phase 4 paper-trading assumptions

- Work resumed at Phase 4 because Phases 0–3 have implementations and Phases 4–6 were stubs. Phases 5 and 6 were not started.
- A paper run date is the signal date. Signals use data through that date's close; the resulting limit orders are filled from the first available session after the signal date, so skipped calendar days catch up without reading future bars.
- Order plans are sized at the signal close using ATR computed only from prior bars. Stops and targets are fixed when the plan is created; the fill price may differ. This keeps the JSON order plan and the ledger consistent.
- The decay monitor uses the worst single closed-trade return as its drawdown proxy because approvals store per-trade summaries rather than an equity curve. Portfolio-level marked drawdown is shown in the report for information only.
- `paper run` validates the data slice it consumes but does not rerun the full multi-year health report; `nse-engine data check` remains the authoritative quality gate.
- An empty `approved_setups.yaml` is a valid no-trade state. Paper reports contain no timestamps so a repeated idempotent run reproduces identical files.

# Real-data verification assumptions

- `random_signal` draws are hashes of `(seed, symbol, session date)` only. They are deterministic, independent of OHLCV history, and expected to fail every gate.
- The point-in-time stock universe ranks symbols by trailing median traded value using only data available on each date; whitespace-containing EOD2 index labels are excluded by configuration.
- `CCAVENUE` had a 10:1 share consolidation effective 2026-10-13, with trading resumed 2026-10-19. EOD2 did not back-adjust the bars, so returns spanning that date are excluded from interpretation.
- The event-study fast path vectorizes the same fill and charge rules as the single-event simulator; equivalence is covered by tests, and registry trials are appended rather than overwritten.
- Paper runs materialize session bars only for the signal date and the first observed session after each pending order. Delayed runs therefore still fill from the first session after the order date, and future bars cannot affect earlier fills.

# Phase 1 point-in-time data upgrade

- No licensed point-in-time (PIT), delisted-inclusive, total-return dataset is present locally. This phase adds the importer, adjustment engine, membership loader, and verification command; it does not invent new market data. `config/research.yaml` stays `SURVIVOR_BIASED` until the owner supplies and verifies real PIT data.
- A PIT bars file contains raw OHLCV rows for every symbol it covers, including delisted symbols. Membership effective dates are supplied in a separate CSV so a symbol can enter or leave the investable universe without deleting its bars.
- Corporate actions are supplied as a CSV with an explicit positive `price_factor` applied to all bars strictly before `ex_date`: 0.5 for a 2:1 split or 1:1 bonus, 10.0 for a 10:1 consolidation. Volume is divided by the same factor. The owner must verify each action against exchange notices; the engine only checks arithmetic and return continuity.
- Importing PIT data into the shared store intentionally overrides overlapping EOD2 bars because `pit_adjusted` has higher source priority. EOD2 remains available as a comparison source and is never silently deleted.
- `data verify-pit` writes evidence and fails when required PIT membership or corporate-action coverage is missing. It never edits the bias status in YAML; changing `SURVIVOR_BIASED` to `UNBIASED` remains a manual, reviewable decision.
- Retroactive adjustment is for historical return continuity only and never creates an order. Bars on and after an ex-date keep their raw prices, and research reads remain date-bounded through `MarketDataStore.read_ohlcv_through`.

# Provisional kill-stage studies

- Provisional mode is a Phase 2 decision rule on survivor-biased free data, not a new data claim: a failed setup is discarded because bias would flatter it, while a pass is only a candidate for later verification on PIT data. `research.data_bias_status` stays `SURVIVOR_BIASED`.
- Stock setups in this mode use the top-100 trailing traded-value universe defined in `config/universe_provisional.yaml`; ETF-level setups keep their configured symbol lists. The tighter screen reduces the expected small-cap survivorship inflation during the kill stage.
- Provisional runs tag registry rows with `provisional: true`, allow only train and validation splits, block holdout before any registry access, and force the bias status in the gate evaluation. The report can show `provisional_pass` or `rejected` only.
- `study report --provisional` writes `reports/study_report_provisional.html` with a prominent `SURVIVOR_BIASED` banner and never writes or deletes `approved_setups.yaml`, even if an approval file already exists.

# Additional Phase 2 hypotheses

- `delivery_spike` compares the current `delivery_pct` with its own trailing average shifted by one bar, requires a positive close, and uses only predeclared YAML multiples. Positive close is `close > open`; the trailing average excludes the spike itself.
- The first import's delivery fields were null because of an **importer mapping bug**, not because EOD2 lacks delivery data. Real EOD2 daily CSVs use the header `DLV_QTY` (plus `TOTAL_TRADES`, `QTY_PER_TRADE`) and no delivery-percentage column. The importer only aliased `deliv_qty`/`deliv_per`, so it silently dropped `DLV_QTY`. The fix adds the `DLV_QTY` alias and derives `delivery_pct = delivery_qty / volume * 100` from the same row, never from future bars. A fixture with the real EOD2 header is covered by tests.
- After re-importing the full EOD2 history, `delivery_qty` and `delivery_pct` are non-null for 3,789,692 of 7,861,421 rows (48.21%). Coverage is 0% before 2019, 24.8% in 2019, then 96.3%–97.4% from 2020 through 2026. Because the locked train split ends in 2017, `delivery_spike` still cannot pass the two-split gates: train has zero delivery events, while validation (2018–2022) has events only from 2019 onward. Its verdict remains a coverage rejection, not proof against the accumulation hypothesis. The earlier all-null trials remain in the append-only registry as historical evidence of the bug.
- `relative_strength_regime` defines strength as the stock's trailing percentage return minus the benchmark's over the same lookback, rather than a normalized price ratio, so a late-starting benchmark cannot make the whole series undefined. The benchmark close is injected into each per-symbol study group by `build_event_study` as `_benchmark_close`, aligned by date from the configured benchmark symbol.
- The regime filter reuses `regime_labels`: the benchmark must be above its configured long SMA and outside the high-volatility tercile. Regime labels use expanding quantiles and trailing windows, so no future benchmark bar enters a signal at close of day t.
- Sweep grids for both setups are the small predeclared lists in their YAML files. No threshold was changed after seeing results, holdout stays locked, and all trials are appended to the existing registry.

# Factor study pre-registration

- `docs/FACTOR_STUDY_PREREG.md` was written on 2026-11-18 in response to "what should be next?" and committed as a frozen design before any data access or study code. It records cross-sectional hypotheses, a point-in-time delisted-inclusive universe, long-only top-quintile equal-weight construction with the existing causal fill model, announcement-dated fundamentals with a restatement-leak fixture test, equal-weight-universe and NIFTYBEES benchmarks, the locked gate strictness, and a stop rule.
- "Go next" was interpreted as owner authorization to fill the previously unfilled decision slots with conservative defaults: value (earnings yield), quality (ROE), momentum (12-1 month), and their equal-weight composite; top-200 universe, top quintile, monthly rebalance, 50% one-way turnover cap, one-session announcement lag, one-session signal-to-order lag, and a ₹0 tuition budget. The zero budget repeats the owner's earlier explicit answer that no personal data budget exists.
- `config/factor.yaml` holds every new factor-study threshold. Existing cost and gate thresholds remain in `config/costs.yaml` and `config/gates.yaml`. A guard test checks that no owner-decision placeholders remain and that both the signal-to-order lag and announcement lag are at least one session.
- This commit freezes design only. It does not start data acquisition, vendor contact, study code, a factor backtest, or any change to `approved_setups.yaml`, `data_bias_status`, or holdout state. The closed daily-price/volume verdict is unchanged. If the owner later changes any frozen slot, the pre-registration must be amended before data access and any subsequent study re-registered.

# Factor-study readiness gate

- The following "go next" was interpreted as authorization to add the readiness layer only: validated factor config, a SHA-256 hash of the frozen document, point-in-time input paths, and a `factor check` CLI that refuses execution until those inputs exist.
- At that step `config/factor_data.yaml` used null paths for PIT prices, announcement-dated fundamentals, and membership. The later acquisition-bot step named local output paths but did not create the inputs, because no such data is in the repo and the ₹0 budget forbids vendor contact.
- `nse-engine factor check` only reads config, checks file existence, verifies the frozen hash, and reads the registry for holdout state. It computes no factors, places no orders, writes no trials, and does not access holdout data.
- The readiness gate is not a factor study run. Actual execution still requires a separate owner decision to supply PIT data, plus a re-verification that the frozen hash still matches before any code consumes it.

# PIT acquisition bot

- "Let achieve this by script or bot in this solution" is interpreted as authorization to add local, config-driven acquisition commands under `factor acquire`: dated NSE bhavcopy downloads, PIT top-200 membership generation, and announcement-metadata/filing downloads.
- The bot uses only public NSE endpoints and no credentials, no vendor spend, and no live order endpoint. URL templates, date bounds, weekdays, delays, retries, raw/filing directories, and exclusions live in `config/factor_data.yaml`; the frozen factor design and ₹0 budget are unchanged.
- Bhavcopy files are stored as immutable dated snapshots with a `.zip` suffix before the configured UDiFF boundary and `.csv` on and after it. The membership builder ranks symbols using only rows with date <= the rebalance date and a trailing window, so later delistings or late liquidity spikes cannot leak backwards.
- The announcement bot downloads metadata and linked filing PDFs; it does not yet extract EPS/net-profit/equity fields. Filing paths derive from a SHA-256 digest of the URL so repeated runs are deterministic. A future parser must append announcement-dated rows and never overwrite prior values, so restatements cannot leak into earlier rebalances.
- No network download is executed by `make check`. Tests use mocked HTTP transports and synthetic local files. Running `factor acquire prices` is an explicit owner action.

# Remaining phases (5 and 6)

- "Complete the pending phases first" is interpreted as Phase 5 (UI and reports) and Phase 6 (broker interface, live disabled), the two phases marked Not started in `docs/STATUS.md`. Phase 3's real-data portfolio run remains blocked because `config/approved_setups.yaml` is empty and holdout is locked; it is a research outcome, not an implementation gap, and is not fabricated.
- Phase 5 reads from existing stores only: no new business logic in the UI layer, and every page shows the research disclaimer and the configured survivorship-bias status.
- Phase 6 keeps live trading disabled: the Groww adapter is a skeleton whose order methods always raise `LiveTradingDisabled`, credentials come only from environment variables, and no code path may call a live order endpoint. `DRY_RUN` defaults to true.
- Neither phase changes `approved_setups.yaml`, `data_bias_status`, the append-only trial registry, or holdout state, and neither reopens the closed daily-price/volume verdict.
