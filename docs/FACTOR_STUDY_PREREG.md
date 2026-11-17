# Factor study pre-registration (COMMITTED)

Status: **COMMITTED before any data access or study code (2026-11-18).** Parameters are frozen. Any edit to hypotheses, universe, construction, benchmarks, gates, or the stop rule after data access invalidates the study.

Research output, not advice; no guarantee of profit.

## Purpose

Test whether a small set of cross-sectional factors produces a net edge after costs on Indian equities using point-in-time data. This is a **new hypothesis class**; it does not reopen the closed daily-price/volume rule program, whose verdict was limited to generic price and volume rules on survivor-biased data.

All tunable values are in `config/factor.yaml`. Existing shared gates remain in `config/gates.yaml` and costs in `config/costs.yaml`. No value is hard-coded in future study code.

## 1. Hypotheses (frozen, four slots)

Each hypothesis is fixed before data access. No hypothesis may be added, dropped, or redefined after data access.

- **H1 value:** `trailing_12m_diluted_eps / price`, eligible only after the configured announcement lag. Expected sign: **positive**. Rationale: a higher earnings yield per rupee of price is compensation for ownership risk, so the cross-section should reward it.
- **H2 quality:** `trailing_12m_net_profit / average_shareholders_equity` (ROE), eligible only after the configured announcement lag. Expected sign: **positive**. Rationale: persistently profitable firms should earn a quality premium relative to weak balance sheets.
- **H3 momentum:** `close[t-21] / close[t-252] - 1`, skipping the most recent month. Expected sign: **positive**. Rationale: medium-horizon price trends in liquid equities tend to persist after a short reversal window.
- **H4 composite:** equal-weight cross-sectional z-score of H1, H2, and H3 after winsorization at the configured fraction. Expected sign: **positive**. Rationale: combining imperfect, differently timed signals should reduce single-factor noise; it is pre-registered as a distinct hypothesis, not a post-hoc rescue.

## 2. Universe

- Point-in-time top **200** names by traded value over the configured 60-session lookback, recomputed at each rebalance from information available as of that date.
- **Delisted-inclusive:** names that later disappear remain in the historical universe. A universe reconstructed from today's survivors is inadmissible.
- Exclude index series, ETFs, and non-equity/non-tradable symbols.
- Membership is as-of the rebalance date; no future-based reconstitution.
- EOD2 survivor-biased data cannot be used for this study.

## 3. Portfolio construction

- Long-only.
- Rank by factor within the universe; hold the top **quintile** (`basket_fraction: 0.2`).
- Equal weight within the selected basket.
- Rebalance **monthly**; orders execute at the next-session open after the signal, using the engine's existing causal fill model. `signal_to_order_lag_sessions: 1` enforces this.
- Turnover cap: **50% one-way** per rebalance.
- Factor inputs are winsorized at the configured fraction before z-scoring.
- All costs and thresholds come from `config/factor.yaml`, `config/costs.yaml`, and `config/gates.yaml`; no magic numbers in code. The **2.0x** stress multiplier in `config/costs.yaml` is applied and may not be lowered after data access.

## 4. Point-in-time discipline for fundamentals

- Fundamental inputs must be **announcement-dated**, with a configured one-session disclosure lag before a value becomes eligible for a rebalance.
- Restatements and backfills must not leak into earlier rebalances. A fixture test must prove that a restated value cannot affect a rebalance dated before its announcement.
- A factor computed from today's as-reported database is inadmissible. This is the fundamental-data equivalent of the look-ahead that the positive-control test demonstrated for prices.

## 5. Benchmarks

- Equal-weight version of the **same** point-in-time universe (controls for size/equal-weight effects).
- NIFTYBEES (cap-weighted reference).
- Beating NIFTYBEES alone is not sufficient evidence; the equal-weight universe comparison is required.

## 6. Gates (locked; same strictness as the closed program)

- Benjamini–Hochberg correction at `fdr_alpha: 0.05` across all hypothesis × horizon combinations.
- Horizons: 1, 3, 6, and 12 months.
- Cost stress at 2.0x.
- Minimum 24 independent rebalance dates.
- Minimum positive-year fraction and positive-symbol breadth; maximum single-year profit concentration, using `config/gates.yaml`.
- Parameter-neighbour stability is not applicable: H1–H4 have fixed definitions and no search grid. Post-hoc parameter changes are forbidden.
- Results reported by calendar year.
- Locked holdout, accessible only with the explicit unlock flag and an audited registry row.
- A pass is a candidate requiring point-in-time verification, never an automatic approval.

## 7. Stop rule

- If no hypothesis passes every gate, close out, record the result, and spend nothing further on data or infrastructure.
- Budget cap: **₹0 tuition**. No vendor contact, quote request, or purchase follows from this pre-registration. Any future data spend requires a separate, explicit owner decision recorded before that work starts.

## 8. Explicit non-goals

- No Groww API subscription, static IP, or algo-compliance work; rebalances are manual, a handful of times a year.
- No live orders. Phase 6 is complete but live trading remains disabled; `DRY_RUN` discipline is unchanged.
- No changes to `config/approved_setups.yaml`, `research.data_bias_status`, or holdout state arise from this study.
- No reuse of the rejected daily-price/volume rules.

## 9. Execution prerequisite

No point-in-time delisted-inclusive price source and no announcement-dated fundamental source are currently available in the repo. Therefore no factor-study code may run and no vendor may be contacted under the ₹0 budget. This document only freezes the design so that execution can be considered without re-deciding the hypothesis after seeing data.

## 10. Disclaimer

Research output, not advice; no guarantee of profit.
