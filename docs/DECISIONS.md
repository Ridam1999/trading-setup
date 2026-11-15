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
