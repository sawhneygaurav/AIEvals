# Fundamentals pass/fail analysis

Saved run: `2026-09-07-all-cases-01`. No new model calls were made for this analysis.

**32/42 field checks pass (76.19%).** Complete-case accuracy is 60.00%. There are 32 correct asserted facts and 0 false-positive assertions. The outcome buckets below explain the failed checks.

## By scenario

| Category | Slots | Pass | Fail | Accuracy | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| All values available | 7 | 7 | 0 | 100.00% | 100.00% | 100.00% | 100.00% |
| Missing fields | 7 | 4 | 3 | 57.14% | 100.00% | 100.00% | 100.00% |
| Two reporting years | 14 | 7 | 7 | 50.00% | 100.00% | 50.00% | 66.67% |
| Consolidated vs standalone | 7 | 7 | 0 | 100.00% | 100.00% | 100.00% | 100.00% |
| Mixed input units | 7 | 7 | 0 | 100.00% | 100.00% | 100.00% | 100.00% |

Each scenario has one case. In the missing-fields scenario, precision and recall cover the four available financial facts; accuracy also counts required unavailable responses. This allows precision/recall/F1 to be 100% even when some required responses fail.

## By financial category

| Category | Slots | Pass | Fail | Accuracy | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Return ratios | 12 | 8 | 4 | 66.67% | 100.00% | 80.00% | 88.89% |
| Profit and loss | 12 | 10 | 2 | 83.33% | 100.00% | 83.33% | 90.91% |
| Balance sheet | 12 | 10 | 2 | 83.33% | 100.00% | 83.33% | 90.91% |
| Operating cash flow | 6 | 4 | 2 | 66.67% | 100.00% | 80.00% | 88.89% |

## By financial field

| Category | Slots | Pass | Fail | Accuracy | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| borrowings | 6 | 5 | 1 | 83.33% | 100.00% | 83.33% | 90.91% |
| operating_cash_flow | 6 | 4 | 2 | 66.67% | 100.00% | 80.00% | 88.89% |
| pat | 6 | 5 | 1 | 83.33% | 100.00% | 83.33% | 90.91% |
| revenue | 6 | 5 | 1 | 83.33% | 100.00% | 83.33% | 90.91% |
| roce | 6 | 4 | 2 | 66.67% | 100.00% | 80.00% | 88.89% |
| roe | 6 | 4 | 2 | 66.67% | 100.00% | 80.00% | 88.89% |
| shareholders_equity | 6 | 5 | 1 | 83.33% | 100.00% | 83.33% | 90.91% |

## Outcome buckets

| Bucket | Result | Count | Share of all slots | Share of failures |
|---|---|---:|---:|---:|
| correct_value | PASS | 32 | 76.19% | N/A |
| missing_unavailable_response | FAIL | 3 | 7.14% | 30.00% |
| missing_requested_period | FAIL | 7 | 16.67% | 70.00% |

These buckets are assigned after observing the outcome. Precision/recall are not calculated within failure-only buckets: doing so conditions on failure and gives misleading or undefined scores. The scenario and financial-category tables above provide meaningful denominators.

There are 7 missing-period failures and 3 missing explicit unavailable responses. In the saved all-cases run, the missing periods are FY2025. The production runner does not forward that case's question, and final metrics retain the latest value per field. This is a request/output interface gap, not proof that the model ignored a two-year instruction. The missing-unavailable failures are ROE, ROCE and CFO in the reduced-evidence case. It avoids invented numbers but omits explicit abstentions.

| Bucket | Suggested correction |
|---|---|
| missing_unavailable_response | Return an explicit unavailable state for each requested unsupported field, with a reason. |
| missing_requested_period | Pass requested years into the agent and retain facts by field, date and accounting basis. |

## Supporting scores

| Category | Numeric accuracy | Unit accuracy, provided facts | Unit coverage | Explicit unavailable accuracy | Citation validity | Faithfulness |
|---|---:|---:|---:|---:|---:|---:|
| Overall | 82.05% | 100.00% | 82.05% | 0.00% | 100.00% | 100.00% |
| Return ratios | 80.00% | 100.00% | 80.00% | 0.00% | 100.00% | 100.00% |
| Profit and loss | 83.33% | 100.00% | 83.33% | N/A | 100.00% | 100.00% |
| Balance sheet | 83.33% | 100.00% | 83.33% | N/A | 100.00% | 100.00% |
| Operating cash flow | 80.00% | 100.00% | 80.00% | 0.00% | 100.00% | 100.00% |

## Definitions and limits

- **Accuracy:** passed requested slots / measured requested slots, including explicit unavailable requirements. This is field pass rate, not binary classification accuracy using true negatives. Pending reviews and execution errors are excluded.
- **Precision:** correct asserted financial facts / asserted financial facts. Wrong values, units, identities and duplicates reduce it.
- **Recall:** correct asserted financial facts / available expected facts. Unsupported expected fields are assessed separately.
- **F1:** 2 × TP / (2 × TP + FP + FN). This run has 32 TP, 0 FP and 7 FN; the 3 missing/incorrect abstentions are separate.
- **Numeric accuracy:** matching numbers / available expected facts, using ±5% relative tolerance and exact zero.
- **Unit coverage:** available expected facts returned with the exact requested unit / available expected facts. Unit accuracy on provided facts excludes omitted values, so read both scores together.
- **Faithfulness:** supported asserted requested facts / asserted requested facts. The existing Codex source checks were verified against their input/output hashes and reused. They are not independent human labels.
- **N/A:** no eligible observations or no completed evidence review; never silently treated as zero or 100%.

Field-weighted accuracy is 76.19%; equal-scenario average accuracy is 81.43%. The two-year case has 14 slots, twice the weight of each other case in the field-weighted result. Related PNGJL examples reuse one report. Five development cases with one example per scenario do not establish performance on unseen companies or future runs.

Precision measures correctness of asserted financial facts and does not establish completeness. Use the outcome buckets to choose the next correction, and keep source support separate from numerical accuracy.

The [CSV](golden_dataset_with_predictions.csv) adds scenario, financial category, result category and suggested correction columns. Human comments and the original expected/predicted/results columns are preserved. [Structured metrics](result_analysis.json) include counts, denominators, row membership and source fingerprints.
