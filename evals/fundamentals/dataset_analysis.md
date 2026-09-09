# Dataset balance and coverage

Snapshot of the dataset reviewed on 7 September 2026.

Scope: **5 development cases and 42 expected field slots**. All five are authorized for this pilot and have been run. Codex checked the additional answer keys; this is not independent human labeling.

Here, skew means uneven coverage of labels, scenarios, companies and edge cases. Do not calculate one statistical skewness value over ROE, revenue and cash flow: they measure different quantities and use different units.

## Current distribution

| Dimension | Distribution | What it tells us |
|---|---|---|
| Scenarios, counted by case | One case per scenario; 20% each | Even labels, but only one example of each. |
| Scenarios, counted by field | Two-year scenario: 14/42 (33.3%); each other scenario: 7/42 (16.7%) | A field-weighted aggregate gives the two-year case twice the weight. |
| Requested financial fields | Six slots for each of the seven fields | Counts are even; difficulty and availability are not equally covered. |
| Expected availability | 39/42 available (92.9%); 3/42 unavailable (7.1%) | Available values outnumber missing values 13:1. |
| Expected accounting basis | 42/42 consolidated; 0 standalone | A standalone distractor exists, but no case asks for a standalone answer. |
| Expected dates | 35/42 end in March 2026 (83.3%); 7/42 in March 2025 (16.7%) | Prior-year answers occur only in the two-year scenario. |
| Source type | Three real-report cases; two synthetic cases | Real examples are all derived from one company report. |
| Review status | Five cases approved for this pilot | Additional answer keys checked by Codex after the user authorized all cases. |
| Split | Five development cases; no holdout cases | This suite cannot estimate generalization to unseen cases. |

The all-available rule would score **92.9% on availability classification alone**, while missing all three required abstentions. This does not establish numerical accuracy or pass any financial fact by itself. Report unavailable accuracy separately.

## Coverage within each field

| Field | Total slots | Available | Unavailable | Negative values | Zero values |
|---|---:|---:|---:|---:|---:|
| roe | 6 | 5 | 1 | 0 | 0 |
| roce | 6 | 5 | 1 | 0 | 0 |
| revenue | 6 | 6 | 0 | 0 | 0 |
| pat | 6 | 6 | 0 | 0 | 0 |
| shareholders_equity | 6 | 6 | 0 | 0 | 0 |
| borrowings | 6 | 6 | 0 | 0 | 0 |
| operating_cash_flow | 6 | 5 | 1 | 3 | 1 |

Missing-data coverage currently exercises ROE, ROCE and CFO only. There are no missing revenue, PAT, equity or borrowings examples. All three negative-value entries are CFO; no case covers a net loss or negative return ratio. The zero example is synthetic CFO. These are entries, not independent source reports.

## Reused evidence

The real cases contain **25 available fact entries** representing **14 distinct company/field/date/basis/unit/value combinations**. That is **11 repeated entries** across deliberately related variants.

This reuse helps isolate missing-data and year-selection behavior. It does not provide the diversity of additional companies or independent reports. Keep these related cases in the same development/holdout split to avoid evidence leakage.

All five cases have now been run. The missing-data and two-year cases expose gaps in the current production output contract. All output facts still target consolidated statements; running the suite does not add company or target-basis diversity.

## Add coverage where it is missing

1. Add independent reports from additional real companies.
2. Add missing-data examples for revenue, PAT, equity and borrowings.
3. Include questions that explicitly require standalone answers, alongside consolidated ones.
4. Add loss-making, negative-ratio and near-zero examples with well-defined grading rules.
5. Include quarter/annual selection and genuinely conflicting evidence if those are in the agent's intended scope.
6. Reserve independent company/report families for a holdout set once the dataset is large enough.

Do not force every category to 50/50. Choose coverage targets from the intended workload, and keep a separately labelled set of rare but important failure cases. Show both per-case averages and per-field totals so large cases do not silently dominate.

## Where this fits in the evaluation flow

**Golden dataset → balance and coverage check → run agent → expected/predicted table → error analysis.**

For intent-routing agents, show expected-intent counts before the run, then predicted-intent counts, a confusion matrix, and precision/recall/F1 for each intent after the run. Compare these to detect a model that over-predicts one label. Also show an average giving each intent equal weight, so frequent labels do not hide failures on rare ones. No routing-intent dataset has been measured here.

The current **32 PASS / 10 FAIL / 0 NOT_RUN** field counts come from the [all-cases run](all_cases_report.md). They describe agent output against the proposed contract, not dataset balance. All five executions completed without provider errors. Refreshing these distributions does not make a model request.

Sources: [case references](suite_references.jsonl), [case evidence](suite_inputs.jsonl), [prediction snapshot](golden_dataset_with_predictions.csv). [Structured analysis](dataset_analysis.json) contains the computed distributions and input fingerprints.

The analysis script refreshes the structured distributions. This written interpretation
is a dated snapshot and should be reviewed when the dataset changes.
