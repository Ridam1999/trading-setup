# Decisions

## Phase 0

- Use a `src/` package layout and expose the CLI as `nse-engine`.
- Load configuration from YAML files in `config/`, validating the shared base settings with Pydantic and retaining other YAML documents as typed mappings for later phase schemas.
- Keep generated data, reports, logs, local environments, and secrets out of version control.
- Phase 2 setup evaluation uses explicit class registration, causal per-symbol indicators, next-session limit entry, stop-first daily-bar ambiguity resolution, date-clustered inference, and an auditable holdout unlock event.
