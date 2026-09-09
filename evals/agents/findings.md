# Baseline findings — 9 September 2026

**42/44 development cases passed (95.45%).** All 206 deterministic checks passed.
The separate LLM judge passed 34/36 task criteria (94.44%) and labelled 39/39
factual output spans supported. Four pure process/abstention spans were excluded
from that support denominator. There were no uncertain verdicts and no actor or
judge errors in the selected final runs.

These are synthetic development results, not estimates of real-world investment
research accuracy. There are no real-company evidence cases, holdout cases or
human-approved cases in this new suite. The judge used the same model as the
actor in separate calls. Its 100% factual-span support does not mean every task
was completed, every investment score was warranted, or every source was retrieved.

| Agent/stage | Cases passed | Main coverage |
|---|---:|---|
| Business | 4/5 | Footprint, plans versus execution, missing evidence, source conflict, injection |
| Management | 5/5 | Inquiry limits, execution/funding, missing pledge, rating history, missing evidence |
| Valuation | 4/5 | Reported ratios, wrong period, stale evidence, source conflict, missing ratios |
| Technicals | 6/6 | Adjusted/raw price selection, monotonic/flat indicators, future cutoff, short history |
| Fixed peers | 3/3 | Fixed universe and unsupported scope |
| Source collection | 4/4 | Ordering, deduplication, future results, failure visibility, manual-only evidence |
| Book policy | 5/5 | Complete/80%/60%/zero coverage and missing company evidence |
| Evidence auditor | 4/4 | Clean result, dangling citation, low confidence, provider warning |
| Orchestrator | 2/2 | Company/book barriers and suppression of invalid rankings |
| Score combiner | 5/5 | Weights, missing component/growth, manual-review gate |

## Failure 1: Business does not clearly prefer primary evidence

`BUSINESS-003` supplies a company statement reporting 45 stores and a same-date
secondary blog reporting 90. The answer includes both counts but describes the
operating evidence as unreliable. It does not adopt 45 as the primary reported
count while identifying 90 as the conflicting secondary claim.

The correct output can remain **insufficient evidence for an overall business
score**. That does not prevent it from reporting the supported primary-source
fact. The failure is source preference, not a missing number or the decision to
withhold a broad investment assessment.

The actor's existing shared instructions already say to prefer primary evidence.
Its raw Business slice and final block contain the same wording, so this is not
a demonstrated post-processing loss. A more explicit conflict-handling instruction
is a reasonable candidate, but improvement has not been measured. See the
[proposed Business prompt change](prompts/business_conflicts_candidate.md).

Evidence: [final and raw output](../../outputs/evaluations/agents/2026-09-09-live-02/BUSINESS-003/actual.json),
[delivered actor prompt](../../outputs/evaluations/agents/2026-09-09-live-02/BUSINESS-003/calls.json),
[judge rationale](../../outputs/evaluations/agents/2026-09-09-live-02/BUSINESS-003/judge/response.json).

## Failure 2: Valuation loses conflict disclosure

`VALUATION-004` supplies an exchange TTM P/E of 24x and a same-date blog value of
12x. The actor correctly selects 24x from the exchange. Python produces the
expected 72.1 score using the existing growth-score fallback of 50. All eight
deterministic checks for this case pass.

The final answer never mentions the 12x disagreement. The shared dossier schema
holds valuation observations only, and its prompt requests at most one observation
per valuation code. `_build_live_valuation_from_observations()` generates a new
formula summary and generic strengths/risks; there is no place in this path to
carry a source-conflict explanation to the final reader. The standalone valuation
prompt's instruction to describe conflicts is not the shared-dossier path tested
here.

This needs an extraction/output-contract change plus final-output handling.
A prompt edit alone cannot preserve text that the schema cannot return. The
[candidate contract and prompt](prompts/valuation_conflicts_candidate.md) describes
the proposed change. It is drafted, not deployed or benchmarked.

Evidence: [selected observation and final output](../../outputs/evaluations/agents/2026-09-09-live-02/VALUATION-004/actual.json),
[delivered prompt/schema](../../outputs/evaluations/agents/2026-09-09-live-02/VALUATION-004/calls.json),
[judge rationale](../../outputs/evaluations/agents/2026-09-09-live-02/VALUATION-004/judge/response.json).

## Passing behavior and remaining questions

Codex checked both failures against their frozen evidence and inspected passing
Business footprint/injection cases, Management inquiry/pledge/rating cases, and
the selected Valuation observations. This was not an independent human review.
The raw judge responses remain unchanged. No failure was regraded to improve the
headline result.

The Management pledge case preserves 62% promoter holding and correctly treats
a blank pledge field as unknown. It nevertheless assigns a management score of
50 with confidence 0.50 while acknowledging limited evidence. The current rubric
tests factual interpretation, not whether that evidence is enough for a broad
quality score. Define a separate scoring-sufficiency policy before calling that
score correct or incorrect; add cases before changing that gate. Do not infer
that Management needs no further evaluation from five factual passes.

Likewise, the Valuation growth fallback is an existing scoring assumption, not
observed company growth. The judge receives that context. The final wording could
label the assumption more clearly; this report does not claim it was measured as
a separate failure. Peer/source/book/audit/graph passes concern their stated
software contracts. In particular, no private-book semantic retrieval, live
internet relevance, real-company filing extraction, or oscillating-price RSI
case was measured.

## Execution and reproducibility

- Final runs: `2026-09-09-offline-02` and `2026-09-09-live-02`.
- Live model: Nebius `moonshotai/Kimi-K3`, chat completions, low reasoning,
  one provider attempt per logical call.
- 15 actor calls and 15 separate judge calls; 140.70 seconds summed across the
  15 case executions, including grading/serialization. This is not whole-session
  elapsed time or a production refresh benchmark.
- All answer keys were authored before model outputs. Exact prompts, schemas,
  raw responses, input/reference/output hashes and production source hashes are
  saved per case under ignored `outputs/evaluations/agents/`.
- The preliminary offline run exposed a missing `pages` field in the synthetic
  book adapter. That runner defect was corrected before the final offline run.
  The preliminary sandboxed live run failed at the provider boundary. Both
  preliminary runs remain saved and are excluded from the final quality table.
- Production agent behavior, scoring weights and the Fundamentals suite were
  not changed by this work. The changes are evaluation code, fixtures and reports.

See [the complete results](results.md), [all expectations/predictions and review
comments](expected_vs_predicted.csv), and [dataset balance and gaps](dataset_analysis.md).
