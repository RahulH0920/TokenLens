# LLM FinOps Validation Report: Comparing Dashboard Totals with Known Expected Values
**Overall Status:** `PASS WITH WARNINGS`

## 1. Scope & Audit Metadata
- **Dataset File:** `sample_requests.csv`
- **Source Records:** 1,208
- **Valid Processed Records:** 1,200
- **Rejected Records (includes duplicate rows):** 8
- **Dataset SHA-256 Prefix:** `bcab906d6394841b`
- **Pricing Table Version:** `v2026.10`
- **Pricing File SHA-256 Prefix:** `c877a39e0674125a`
- **Audit Timestamp (UTC):** `2026-10-10T03:22:21Z`

## 2. Validation Results Matrix (Expected vs Actual Dashboard Totals)
All financial comparisons use unrounded Decimal amounts. Display values are rounded to six decimal places.

| Check | Category | Expected | Actual | Difference | Tolerance | Status |
| :--- | :--- | ---: | ---: | ---: | :--- | :---: |
| Source Row Reconciliation (Source = Loaded + Rejected) | Ingestion Integrity | 1208 | 1208 | 0 | Exact (0) | PASS |
| Duplicate Request IDs | Ingestion Integrity | 0 | 5 | 5 | 0 duplicate occurrences allowed | WARNING |
| Total Valid Requests | Dataset Totals | 1200 | 1200 | 0 | Exact (0) | PASS |
| Total Input Tokens | Token Volume | 6,161,501 | 6,161,501 | +0 | Exact (0) | PASS |
| Total Output Tokens | Token Volume | 1,269,818 | 1,269,818 | +0 | Exact (0) | PASS |
| Grand Total Spend (USD) | Financial Reconciliation | $28.334400 | $28.334484 | +$0.00008400 | ±$0.010000 or ±0.0100% | PASS |
| Team Spend — engineering | Team Financial Reconciliation | $5.189500 | $5.189469 | -$0.00003100 | ±$0.010000 or ±0.0100% | PASS |
| Team Spend — marketing | Team Financial Reconciliation | $4.994200 | $4.994215 | +$0.00001500 | ±$0.010000 or ±0.0100% | PASS |
| Team Spend — product | Team Financial Reconciliation | $5.595100 | $5.595117 | +$0.00001700 | ±$0.010000 or ±0.0100% | PASS |
| Team Spend — research | Team Financial Reconciliation | $4.964800 | $4.964796 | -$0.00000400 | ±$0.010000 or ±0.0100% | PASS |
| Team Spend — support | Team Financial Reconciliation | $5.732200 | $5.732218 | +$0.00001800 | ±$0.010000 or ±0.0100% | PASS |
| Team Spend — unattributed | Team Financial Reconciliation | $1.858700 | $1.858669 | -$0.00003100 | ±$0.010000 or ±0.0100% | PASS |
| Cross-Cut Invariant (Team Sum = Model Sum = Grand Total) | Financial Integrity | $28.334484 | Team: $28.334484 \| Model: $28.334484 | $0.00000000 | Exact Decimal equality (0) | PASS |
| Missing Pricing Exceptions | Exception Auditing | 12 | 12 | 0 | Exact expected count; any unpriced row remains an exception | WARNING |
| Unattributed Requests and Spend | Attribution Integrity | 0 unattributed requests | 89 request(s), $1.858669 | 89 request(s) | All requests attributed or explicitly reviewed | WARNING |

## 3. Team Spend Totals
Expected values come from the golden validation manifest; actual values are unrounded Decimal sums.

| Team | Expected | Actual | Difference |
| :--- | ---: | ---: | ---: |
| engineering | $5.189500 | $5.189469 | -$0.00003100 |
| marketing | $4.994200 | $4.994215 | +$0.00001500 |
| product | $5.595100 | $5.595117 | +$0.00001700 |
| research | $4.964800 | $4.964796 | -$0.00000400 |
| support | $5.732200 | $5.732218 | +$0.00001800 |
| unattributed | $1.858700 | $1.858669 | -$0.00003100 |

## 4. Model Spend Totals
The manifest does not define independent per-model expected totals; these are actual Decimal aggregates and are reconciled against the grand total above.

| Model | Actual |
| :--- | ---: |
| claude-3-5-sonnet | $16.597132 |
| gemini-1.5-flash | $0.123995 |
| gpt-4o | $11.613357 |
| unsupported-legacy-model-v0 | $0.000000 |

## 5. Exceptions and Warnings
- **Duplicate handling:** duplicate IDs are detected; the first occurrence is retained and later rows are rejected. Those rejected rows are already included in source-row reconciliation, so duplicates are not double-counted.
- **Duplicate IDs:** `req_0011`, `req_0021`, `req_0031`, `req_0041`, `req_0051`
- **Missing pricing:** flagged requests are not treated as valid zero-cost results.
  - `unsupported-legacy-model-v0`: 12 request(s)
    - Request IDs: `req_0094`, `req_0280`, `req_0292`, `req_0365`, `req_0445`, `req_0473`, `req_0577`, `req_0667`, `req_0870`, `req_0999`, `req_1077`, `req_1115`
- **Unattributed requests:** 89
  - Request IDs: `req_0001`, `req_0041`, `req_0060`, `req_0066`, `req_0083`, `req_0097`, `req_0103`, `req_0111`, `req_0149`, `req_0150`, `req_0169`, `req_0175`, `req_0195`, `req_0204`, `req_0213`, `req_0215`, `req_0216`, `req_0218`, `req_0221`, `req_0222`, `req_0242`, `req_0280`, `req_0289`, `req_0313`, `req_0343`, `req_0373`, `req_0374`, `req_0376`, `req_0410`, `req_0428`, `req_0429`, `req_0432`, `req_0442`, `req_0449`, `req_0451`, `req_0514`, `req_0524`, `req_0527`, `req_0536`, `req_0561`, `req_0571`, `req_0574`, `req_0584`, `req_0607`, `req_0625`, `req_0629`, `req_0637`, `req_0643`, `req_0650`, `req_0679`, `req_0714`, `req_0716`, `req_0719`, `req_0727`, `req_0752`, `req_0760`, `req_0801`, `req_0802`, `req_0803`, `req_0807`, `req_0824`, `req_0849`, `req_0872`, `req_0885`, `req_0886`, `req_0896`, `req_0904`, `req_0906`, `req_0916`, `req_0917`, `req_0952`, `req_0955`, `req_0966`, `req_0974`, `req_0991`, `req_1012`, `req_1020`, `req_1022`, `req_1033`, `req_1054`, `req_1063`, `req_1067`, `req_1086`, `req_1127`, `req_1129`, `req_1138`, `req_1151`, `req_1154`, `req_1167`

## 6. Independent Spot Checks
Expected request costs are independently recalculated from token counts and the applicable pricing record.

| Request ID | Model | Input Tokens | Output Tokens | Input/Output Rate per 1M | Expected Cost | Actual Cost | Difference | Status |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| `req_0836` | `gemini-1.5-flash` | 1,534 | 124 | $0.075000 / $0.300000 | $0.000165 | $0.000165 | +$0.00000000 | PASS |
| `req_0788` | `claude-3-5-sonnet` | 4,650 | 752 | $3.000000 / $15.000000 | $0.025230 | $0.025230 | +$0.00000000 | PASS |
| `req_0414` | `claude-3-5-sonnet` | 10,907 | 2,393 | $3.000000 / $15.000000 | $0.070081 | $0.070081 | +$0.00000000 | PASS |
| `req_0371` | `claude-3-5-sonnet` | 10,089 | 1,408 | $3.000000 / $15.000000 | $0.052041 | $0.052041 | +$0.00000000 | PASS |
| `req_0478` | `claude-3-5-sonnet` | 6,432 | 1,511 | $3.000000 / $15.000000 | $0.041961 | $0.041961 | +$0.00000000 | PASS |
| `req_0515` | `claude-3-5-sonnet` | 11,216 | 1,741 | $3.000000 / $15.000000 | $0.059763 | $0.059763 | +$0.00000000 | PASS |
| `req_0274` | `gemini-1.5-flash` | 2,169 | 378 | $0.075000 / $0.300000 | $0.000276 | $0.000276 | +$0.00000000 | PASS |
| `req_0179` | `gpt-4o` | 7,737 | 1,094 | $2.500000 / $10.000000 | $0.031824 | $0.031824 | +$0.00000000 | PASS |
| `req_0521` | `gemini-1.5-flash` | 3,049 | 479 | $0.075000 / $0.300000 | $0.000394 | $0.000394 | +$0.00000000 | PASS |
| `req_0793` | `claude-3-5-sonnet` | 6,900 | 655 | $3.000000 / $15.000000 | $0.030614 | $0.030614 | +$0.00000000 | PASS |

## 7. Audit Conclusion
- Passed checks: 12
- Warnings: 3
- Failed checks: 0

**PASS WITH WARNINGS:** totals reconcile within configured tolerances, but visible exceptions remain.

Financial tolerance: pass when the absolute difference is within the configured USD tolerance or the relative difference is within the configured percentage tolerance. Cross-cut team/model/grand totals require exact Decimal equality.

---
*Generated by TokenLens' existing CostEngine and ReconciliationEngine.*