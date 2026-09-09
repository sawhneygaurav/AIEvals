# How we grade the Fundamentals pilot

The unit being graded is one financial fact: **company + field + period/date +
accounting basis + value + unit**. A correct-looking number for the wrong identity
fails. Use one prediction per requested fact; duplicates never create extra credit.

The user accepted ±5% **relative** numerical error and exact output units.
The remaining rules below are the pilot implementation, available for editing.

| Check / metric | Definition | Example |
|---|---|---|
| Number | `abs(actual − expected) <= 0.05 × abs(expected)` | ROE 21% permits 19.95–22.05%, inclusive. |
| Zero | Expected zero requires exactly zero. | CFO 0 does not permit 0.01. |
| Units | `percent` or `INR_crore`, as requested. | Correct equivalent output in million still fails the output-unit requirement. |
| Identity | Company, field, period type, period end, and basis all match. | Standalone PAT cannot replace consolidated PAT. |
| Precision | Correct asserted facts ÷ all asserted facts within the seven requested fields. | Six correct and one wrong gives 6/7. |
| Recall | Correct asserted facts ÷ available expected facts. | Four correct of seven required gives 4/7. |
| F1 | `2 × TP / (2 × TP + FP + FN)` | A wrong answer counts as both an FP and an FN. |
| Numeric accuracy | Requested available slots with a matching number ÷ available slots. | Unit, identity and evidence checks remain separate. |
| Unit accuracy on provided fields | Correct-unit provided facts with matching identities ÷ those provided facts. | Two provided facts can both have correct units while five required facts are missing. |
| Unit coverage | Required available slots containing a provided value with the exact unit ÷ available slots. | This must reach 100% to satisfy all required units. |
| Unavailable accuracy | Explicit, correct unavailable responses ÷ unavailable expected slots. | An omitted field is not an explicit abstention. |
| Citation validity | Asserted facts whose cited IDs all resolve to supplied evidence ÷ asserted facts. | A real citation does not establish that it supports the claim. |
| Faithfulness | Supported asserted facts ÷ asserted facts, after inspecting the cited evidence. | A faithfully quoted previous-year value can still fail the requested-year check. |

TP means an asserted fact matches the requested identity, number and unit. FP is
an incorrect asserted fact, unsupported availability claim with a value, or duplicate
assertion. FN is an available expected fact without a correct assertion. Correct
unavailable responses earn credit only in the separate unavailable metric.

Ratios are fractions from 0 to 1 in result JSON and percentages in the review report.
An empty denominator produces `null` (not applicable), never a fabricated 100%.
With seven available expected values and no assertions, recall and F1 are zero,
while precision is not applicable. A provider failure is an **execution error**,
not a measured zero-quality model answer; exclude it from quality aggregates.

## Faithfulness is reviewed separately

The grader does not call an LLM judge or equate matching citation IDs with
faithfulness. It starts with faithfulness unmeasured. A reviewer inspects each
asserted fact's actual supporting excerpt, marks it supported / unsupported /
unclear, and writes a reason. Unclear claims do not earn support credit.

The evidence review is bound to hashes of the exact input and output so a changed
answer cannot inherit an old judgment. Codex can perform and label the initial
source check, with human calibration still available. This metric covers only
assertions in the seven requested fields; it does not grade narrative prose or
other production scoring metrics.

## Passing a case

Every available requested fact must be correct, every unavailable field must have
an explicit unavailable response, and every asserted fact must have valid,
supporting citations. All required output units must match. If numerical checks
pass but evidence is not reviewed, the case remains `pending_evidence_review`.
There is no weighted aggregate that allows a good ROE to hide a wrong unit.

## First run and reproducibility

The runner calls the current production shared dossier and Fundamentals
post-processing. It records the selected model, settings, source/input/reference
fingerprints, production file fingerprints, exact prompts, raw outputs and per-field
results. It sends **only supplied evidence** to the model; the hidden answer key
never enters a prompt. Each provider call has one attempt; the existing bounded
Fundamentals gap-repair call may also occur. One evaluation trial can therefore
contain two model requests.

The seven-field question is **not injected by the evaluation runner**. The first
baseline recorded the production agent's missing output fields as failures. The
corrected production agent now requests and returns the supported seven fields,
and performs unit conversion before the evaluator sees them. Both versions use
the same frozen evidence and answer key. The evaluator never repairs or fills
predictions. See the [baseline](baseline_report.md) and [fix verification](fix_report.md).

All five development cases were dispatched after the user requested the full
suite and Codex checked the additional answer keys. Tests of the grader use
controlled mutations and make no API requests. These five examples, including
reused evidence, do not estimate holdout accuracy across companies or future runs.
See the [all-cases report](all_cases_report.md).

The two-year and explicit-unavailable expectations expose current output-contract
gaps: the production interface receives company and evidence, not the case question,
and retains latest values while omitting absent observations. Their failed checks
do not establish that a model ignored instructions it was never given.

This follows the task-specific examples, explicit criteria and reviewer calibration
approach in [OpenAI's evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices).

## Category analysis

The [saved-result analysis](result_analysis.md) groups scores by scenario, financial
category and field. Financial categories are return ratios (ROE/ROCE), profit and
loss (revenue/PAT), balance sheet (equity/borrowings), and operating cash flow.
These categories are defined independently of whether a response passed.

Category accuracy is passed requested slots divided by measured requested slots,
including required unavailable responses. Extra assertions affect precision and
complete-case status; they do not add expected slots. Pending reviews and execution
errors stay outside the pass/fail denominator. Counts and denominators are saved
alongside every rate. Group precision, recall and F1 pool TP/FP/FN counts, rather
than averaging case percentages.

Outcome buckets diagnose errors after grading. They show counts and shares of
failures, not precision calculated on failures alone. The additional CSV columns
annotate the existing rows without changing expected values, predictions, grades
or human comments.

Recompute the analysis from an existing saved run without a model request:

```sh
.venv/bin/python scripts/analyze_fundamentals_results.py --run-dir outputs/evaluations/fundamentals/2026-09-07-all-cases-01
```

## Commands

Run an accepted case (uses the model and credential already configured in `.env`):

```sh
.venv/bin/python scripts/run_fundamentals_eval.py --output-dir outputs/evaluations/fundamentals/new-run
```

Regrade saved output without another model request:

```sh
.venv/bin/python scripts/run_fundamentals_eval.py --output-dir outputs/evaluations/fundamentals/new-run --regrade --evidence-review outputs/evaluations/fundamentals/new-run/evidence_review.json
```

Test the evaluator offline:

```sh
.venv/bin/python -m pytest -q tests/test_fundamentals_evaluation.py
```
