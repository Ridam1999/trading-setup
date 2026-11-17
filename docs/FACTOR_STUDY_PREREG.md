# Factor study pre-registration (DRAFT)

Status: **DRAFT — not committed to execution.** The methodology below is fixed before any data access or vendor contact. Slots marked `[OWNER DECISION]` must be filled by the project owner and this document committed **before** any data is acquired or study code is written. After data is seen, no edit may be made to hypotheses, universe, construction, benchmarks, gates, or the stop rule.

Research output, not advice; no guarantee of profit.

## Purpose

Test whether a small set of cross-sectional factors produces a net edge after costs on Indian equities using point-in-time data. This is a **new hypothesis class**; it does not reopen the closed daily-price/volume rule program, whose verdict was limited to generic price and volume rules on survivor-biased data.

## 1. Hypotheses (at most four)

Each slot requires the owner's exact factor definition, expected sign, and a one- or two-sentence economic rationale. No hypothesis may be added, dropped, or redefined after data access.

- H1 value: `[OWNER DECISION: definition, expected sign, rationale]`
- H2 quality: `[OWNER DECISION: definition, expected sign, rationale]`
- H3 momentum: `[OWNER DECISION: definition, expected sign, rationale]`
- H4 composite (optional): `[OWNER DECISION: definition, expected sign, rationale]`

## 2. Universe

- Point-in-time top `[OWNER DECISION: N]` by traded value, recomputed at each rebalance from information available as of that date.
- **Delisted-inclusive:** names that later disappear must remain in the historical universe. A universe reconstructed from today's survivors is inadmissible.
- Exclude index series, ETFs (unless explicitly in scope), and non-equity/non-tradable symbols.
- Membership is as-of the rebalance date; no future-based reconstitution.
- EOD2 survivor-biased data cannot be used for this study.

## 3. Portfolio construction

- Long-only.
- Rank by factor within the universe; hold the top `[OWNER DECISION: quintile or tercile]`.
- Equal weight within the selected basket.
- Rebalance `[OWNER DECISION: monthly or quarterly]`; orders execute at the next-session open after the signal, using the engine's existing causal fill model.
- Turnover cap: `[OWNER DECISION]`.
- All costs and thresholds come from YAML config; the 2.0x stress multiplier is applied. No magic numbers in code.

## 4. Point-in-time discipline for fundamentals

- Fundamental inputs must be **announcement-dated**, with a fixed disclosure lag before a value becomes eligible for a rebalance.
- Restatements and backfills must not leak into earlier rebalances. A fixture test must prove that a restated value cannot affect a rebalance dated before its announcement.
- A factor computed from today's as-reported database is inadmissible. This is the fundamental-data equivalent of the look-ahead that the positive-control test demonstrated for prices.

## 5. Benchmarks

- Equal-weight version of the **same** point-in-time universe (controls for size/equal-weight effects).
- NIFTYBEES (cap-weighted reference).
- Beating NIFTYBEES alone is not sufficient evidence; the equal-weight universe comparison is required.

## 6. Gates (locked; same strictness as the closed program)

- Benjamini–Hochberg correction across all hypothesis × horizon combinations.
- Cost stress at 2.0x.
- Minimum independent rebalance dates.
- Minimum positive-year fraction and positive-symbol breadth; maximum single-year profit concentration.
- Parameter-neighbour stability where parameters exist.
- Results reported by calendar year.
- Locked holdout, accessible only with the explicit unlock flag and an audited registry row.
- A pass is a candidate requiring point-in-time verification, never an automatic approval.

## 7. Stop rule

- If no hypothesis passes every gate, close out, record the result, and spend nothing further on data or infrastructure.
- Budget cap: `[OWNER DECISION: tuition amount]`, set **before** any vendor contact. If a quote exceeds it, stop; no later result may change that decision.

## 8. Explicit non-goals

- No Groww API subscription, static IP, or algo-compliance work; rebalances are manual, a handful of times a year.
- No live orders and no broker interface; Phase 6 remains unstarted and `DRY_RUN` discipline is unchanged.
- No changes to `config/approved_setups.yaml`, `research.data_bias_status`, or holdout state arise from this study.
- No reuse of the rejected daily-price/volume rules.

## 9. Disclaimer

Research output, not advice; no guarantee of profit.
