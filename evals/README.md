# Evaluation dataset planning

The [remaining-agent suite](agents/README.md) now has a separate 44-case synthetic
development baseline: 42 cases passed, with source-conflict failures in Business
and Valuation. Read the [findings and proposed fixes](agents/findings.md),
[results](agents/results.md), and [expected/predicted CSV](agents/expected_vs_predicted.csv).
Its deterministic stages use frozen adapters; its three language agents use live
production model calls plus separate LLM judges. The Fundamentals pilot below is
maintained separately.

We have completed the first Fundamentals evaluation milestone: **five scenarios,
one accepted real case, four review drafts, and one live baseline**.

Start with the [expected-versus-predicted table and pass/fail counts](fundamentals/results_summary.md).
The [CSV](fundamentals/golden_dataset_with_predictions.csv) contains all 42 field checks.
The [dataset balance analysis](fundamentals/dataset_analysis.md) shows scenario,
availability, company and accounting-basis coverage. Check this before a run and
use it again when interpreting the outcome table.
Read the [baseline report](fundamentals/baseline_report.md), then review the
[five-case dataset](fundamentals/suite_review.md) and [metric definitions](fundamentals/metrics.md).

We agreed on seven fields: ROE, ROCE, revenue, PAT, shareholders’ equity,
borrowings, and operating cash flow. Numerical tolerance is **±5% relative**;
output units must match **100%**. Company, financial concept, period and basis
must also match. Management quality remains a separate agent evaluation.

The [accepted answer key](fundamentals/pngjl_fy2026_review.md) is unchanged.
The [suite inputs](fundamentals/suite_inputs.jsonl) contain evidence;
[references](fundamentals/suite_references.jsonl) contain evaluator-only expectations.
The runner supplies verified text excerpts and records the saved PDF fingerprint.
It does not send the hidden reference to the model.

The completed baseline exposed schema and post-processing gaps: the production
output returned none of the seven fields, although the initial model extraction
copied four requested concepts correctly. The report separates these causes and
records a reproduced lakh-to-crore conversion defect. Application behavior has
not been changed to improve this baseline.

Only accepted case 1 was run live. The other four cases remain review drafts.
Cases 1–3 use real PNGJL report excerpts; cases 4–5 use clearly labelled fictional
company data. Run artifacts are in ignored `outputs/evaluations/fundamentals/`.

## Earlier teaching examples

[The editable synthetic cases](starter_cases.md) are background examples from
the earlier walkthrough. Their example-specific thresholds are not the agreed
seven-field Fundamentals policy above.

These six cases are **synthetic drafts**, not a reviewed golden dataset. Every
company figure and source excerpt is invented for teaching. None is a statement
about PNGJL or its peers, and none is a quotation from the private investing book.

## Work through one case together

1. Read the question and the evidence the agent receives.
2. Edit the suggested answer: what should a correct answer contain?
3. Edit the pass/fail rules: what mistakes should fail the case?
4. Tell Codex your edits, or edit `starter_cases.md` directly.
5. We reconcile the editable version with `starter_cases.jsonl`, then record
   your approval for that individual case. Approval is never assumed.

For those teaching examples, the Markdown is the review copy. The JSONL contains the same six cases
in a machine-readable format. They do not synchronize automatically. The empty
[new-case template](new_case_template.json) is for adding your own examples.

## What is in one case?

| Field | Meaning |
|---|---|
| `case_id` | Stable label for the exam question |
| `agent` | Agent or workflow stage being evaluated |
| `category` | Straightforward, tricky, regression, or adversarial |
| `input` | Question, research cutoff, and frozen evidence given to the agent |
| `reference.expected_answer` | Our private answer key |
| `reference.required_facts` | Facts that must be present and correct |
| `reference.pass_when` | Conditions for passing |
| `reference.fail_when` | Specific mistakes that fail the case |
| `reference.grading_method` | Deterministic checks, human review, or both |
| `review` | Draft/approved status, human reviewer, date, and requested edits |

Only `input` goes to an agent. Keep `reference` and `review` out of its prompt.
The reference's structured facts are evaluator expectations, not a replacement
for the production agent's output schema. We will map them to its actual output
when we build the runner.

## Turn drafts into a golden dataset

For a real case, replace the synthetic evidence with a saved, checked source
excerpt. Preserve the source URL or local document identifier, page, publication
date, financial period, units, accounting basis, and research cutoff. Recalculate
the expected facts independently, then review the answer and grading rules.
Do not use an agent's own answer as truth without checking it.

Synthetic cases can also become approved golden cases after review, but keep
their synthetic label. Keep confidential filings and licensed book excerpts in
ignored private storage; the public sample files should contain no private text.

An earlier, unapproved expansion idea was a 40-case mix:
20 straightforward, 12 tricky, 6 regressions, and 2 adversarial inputs. Keep
related variants in the same split so a slightly altered example does not leak
into the holdout set. These six visible teaching cases are all development cases.

The six earlier synthetic teaching examples have not been run against an agent.
The completed live run belongs to the separate Fundamentals pilot above.

The approach follows [OpenAI's evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices):
use task-specific examples, clear expected outcomes, and human calibration of
grading. The cases and marking rules here are our own project-specific drafts.
