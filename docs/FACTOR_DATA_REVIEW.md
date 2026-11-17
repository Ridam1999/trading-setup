# Factor PIT data review (manual blocker)

Research output, not advice; no guarantee of profit.

## Scope acquired

A temporary config bounded all public acquisition to `2005-01-01` through `2022-12-31`, entirely before the locked holdout that starts `2023-01-01`. The frozen `config/factor.yaml` document hash was not changed. The 2026 UDiFF smoke-test file was moved outside the active PIT price layout before acquisition.

| Input | Result |
| --- | --- |
| Dated bhavcopies | 4,446 weekday snapshots, first `2005-01-03`, last `2022-12-30`. |
| PIT membership | 43,200 rows, 200 rebalances, 1,253 symbols, `2005-01-31` to `2022-12-30`. |
| Announcement metadata | 970,721 deduplicated rows, 2,729 symbols, `2005-01-03` to `2022-12-31`. |
| Configured filing candidates | 42,190 PDFs matched the configured financial-result headline patterns; 2,112 symbols. Filings are concentrated from 2018 onward. |

The full filing set was **not** downloaded. A deterministic 200-row diagnostic sample was downloaded instead, solely to check whether the parser can produce trustworthy announcement-dated fields.

## Diagnostic sample outcome

From 200 sampled announcements: 199 PDFs were saved, 1 URL was unavailable. Parsing the 199 saved PDFs produced:

- 13 fundamental rows: 2 diluted-EPS values, 11 net-profit values, and **0** average-shareholders-equity values.
- 187 unparsed files.
- 1 malformed PDF exposed a parser crash; it is now counted as unparsed instead of aborting the batch (`6c767ea`).

Examples requiring manual validation include `SIMBHALS` with an extracted diluted EPS of 37,493,820 and several net-profit values that appear to be single-row or partially parsed numbers rather than trailing-12-month totals. The current label-based extraction cannot establish that a value is annual, consolidated versus standalone, or correctly scaled.

## Blocker

No real factor run is permitted yet. The price and membership inputs are usable as public research inputs, but the fundamental input does not meet the pre-registration requirement that H1 and H2 use announcement-dated, restatement-safe, correctly scaled trailing values. H2 cannot be computed without average shareholders' equity, and the sparse/implausible EPS and net-profit values are not safe to use for H1 or H4.

Manual owner decision needed:

1. Supply or approve a reliable announcement-dated fundamental source for the three frozen fields, or
2. Review the downloaded PDFs and explicitly approve an extraction rule set/error boundary that can be documented without changing the frozen hypotheses.

No trials were appended, no approvals were written, the bias flag is unchanged, and the holdout remains locked.
