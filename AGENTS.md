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
