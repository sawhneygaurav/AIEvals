# All Fundamentals cases — 7 September 2026

All five development cases completed: **3 cases passed and 2 failed**. Across the
42 expected field slots, **32 passed and 10 failed**. No cases remain unrun and
no provider errors occurred. Expected values and human comments are unchanged.

See the [category analysis](result_analysis.md) for scenario, financial-category
and individual-field accuracy, precision, recall, F1 and supporting scores.

| Scenario | Case ending | Field PASS | Field FAIL | Case result |
|---|---|---:|---:|---|
| All values available | 001 | 7 | 0 | PASS |
| Missing fields | 002 | 4 | 3 | FAIL |
| Two reporting years | 003 | 7 | 7 | FAIL |
| Consolidated vs standalone | 004 | 7 | 0 | PASS |
| Mixed input units | 005 | 7 | 0 | PASS |

## Why ten checks failed

**Three missing-field checks:** ROE, ROCE and operating cash flow are absent from
case 002's evidence. The agent correctly avoids inventing their values, but its
final output also omits the required explicit unavailable responses. All four
supported amounts are correct. This is an output-contract gap; unavailable
accuracy is 0/3 under the current grading rules.

**Seven prior-year checks:** case 003 returns all seven FY2026 values but none of
the requested FY2025 values. Its raw extraction includes prior-year PAT and CFO
for cash-conversion history, but the final metrics keep the latest value per code.
The evaluation runner passes company and evidence to the production agent; it
does not pass the case question asking for two years. Therefore this result
exposes a request/output-interface gap, not evidence that the model ignored a
two-year instruction. Cases 001 and 003 supply the same production evidence.

The next implementation changes would be to carry requested periods into the
agent, retain requested facts by field/date/basis, and emit explicit availability
states for requested unsupported fields. These changes were not applied during
this run; the results record the current agent behavior.

## Measured metrics

| Metric | Result | Denominator |
|---|---:|---|
| Field pass rate | 76.19% | 32/42 required slots |
| Complete-case pass rate | 60% | 3/5 cases |
| Micro precision | 100% | 32 correct assertions / 32 assertions |
| Micro recall | 82.05% | 32 correct assertions / 39 available expected facts |
| Micro F1 | 90.14% | 64 / (64 + 0 false positives + 7 false negatives) |
| Unit accuracy on provided facts | 100% | 32/32 |
| Unit coverage | 82.05% | 32/39 available expected slots |
| Explicit unavailable accuracy | 0% | 0/3 unsupported expected slots |
| Citation validity | 100% | 32/32 assertions |
| Source-checked faithfulness | 100% | 32/32 asserted requested facts |

The three omitted unavailable responses are separate from the seven false
negatives for available values. This explains why ten field checks fail while
the financial-fact recall denominator has seven misses. Exact-zero CFO and
mixed lakh/million/crore inputs passed. Source checks were performed by Codex,
not an independent human reviewer. Narrative text and extra investment-scoring
metrics are outside this pilot's faithfulness denominator.

## Execution and reproducibility

Run ID: `2026-09-07-all-cases-01`. Each case used Nebius `moonshotai/Kimi-K3`,
chat completions, low reasoning, and one provider attempt per logical call.
There were 10 logical calls across five sequential runs, taking 50.64 seconds
in total inside the agent calls. All five used the same current production
source fingerprints. No agent code or scoring policy changed for this run.

The user authorized all cases. Codex checked the four additional answer keys
and changed their pilot review status from draft to approved. Every expected
fact remains unchanged. The raw model prompts, outputs, source hashes and
grades are saved separately per case. No retrieval, PDF parsing, full application
refresh or holdout dataset was tested.

## Reading the CSV

The [comparison table](golden_dataset_with_predictions.csv) now contains every
case's result. `NOT_RETURNED` labels the ten omitted predictions for readability.
It is a display marker, not agent-generated text, a fabricated numeric value or
an explicit `UNAVAILABLE` answer. Missing values remain absent in the saved raw
output. Failure reasons explain each omission. Human comments remain editable
and have been preserved.

See the [structured results](../../outputs/evaluations/fundamentals/2026-09-07-all-cases-01/summary.json),
[run manifest](../../outputs/evaluations/fundamentals/2026-09-07-all-cases-01/manifest.json),
and [answer-key review](../../outputs/evaluations/fundamentals/2026-09-07-all-cases-01/answer_key_review.json).
Each case directory contains `calls.json`, `actual.json`, `grade.json`,
`evidence_review.json` and `metadata.json`. The original baseline and first fix
verification remain preserved for comparison.
