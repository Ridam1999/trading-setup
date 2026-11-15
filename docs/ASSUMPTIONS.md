# Assumptions

- The supplied technical guide is the authoritative project specification and is copied verbatim to `docs/SPEC.md`.
- The workspace was not initialized as a Git repository, and `uv` is not installed. No repository initialization, package installation, or commit is performed as part of this bootstrap.
- Dependency versions in `pyproject.toml` are pinned. Phase 1 NumPy is pinned to 2.4.6 because the newer 2.5 line requires Python 3.12 while the specification supports Python 3.11.
- Phase 0 supplies a minimal Pydantic YAML config loader, Typer CLI, and logging setup. It does not implement Phase 1+ behavior.
- Phase 1 treats EOD2 close data as already adjusted and records NSE bhavcopy close in `close_raw`. The calendar models Monday-Friday sessions and relies on the configured holiday list; exceptional weekend sessions must be added explicitly in a later calendar enhancement.
- Universe membership is static unless point-in-time dates are supplied. The default empty symbol list is marked survivorship-biased.
