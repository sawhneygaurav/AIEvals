# LLM judge review: ROE, ROCE and operating cash flow

**18 field checks reviewed: 12 pass, 6 fail, 0 uncertain.** Every judge verdict agrees
with the existing rule-and-source-review grade. The six failures are omissions;
the 12 numeric claims returned are supported by the supplied evidence.

## Category results

| Category | Checks | Pass | Fail | Accuracy | Fact precision | Fact recall | Fact F1 | Judge faithfulness |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ROE / ROCE | 12 | 8 | 4 | 66.67% | 100% | 80% | 88.89% | 8/8 supported |
| Operating cash flow | 6 | 4 | 2 | 66.67% | 100% | 80% | 88.89% | 4/4 supported |
| Total | 18 | 12 | 6 | 66.67% | 100% | 80% | 88.89% | 12/12 supported |

Accuracy includes all requested slots, including unavailable facts. Precision,
recall and F1 use the existing deterministic fact definitions: 12 true positives,
0 false positives and 3 false negatives across 15 expected available facts. They
are **agent fact scores**, not the judge's precision as a classifier. The other
three slots require explicit unavailable answers; unavailable-answer accuracy is
0/3. Units are correct for 12/12 provided values; available-fact coverage is 12/15.
Faithfulness evaluates asserted values, so it does not count omissions as supported.

## What failed and what to change

| Failure group | Affected checks | Trace-supported explanation | Required improvement |
|---|---|---|---|
| Missing unavailable response | Case 002: ROE, ROCE, CFO | Only balance-sheet and P&L evidence was provided. The prompt says to omit unsupported values; the response schema requires a number and cannot express an unavailable fact. | Require one answer per requested slot; add `available=false`, nullable value and a reason to the raw schema; emit unavailable facts in the final output. Do not invent missing ratios or CFO. |
| Missing previous-year ratios | Case 003: FY2025 ROE and ROCE | The question asks for two years, but it is not passed to the production extraction call. The prompt also limits ratios to one observation per code and prefers the latest value. Final selection retains only the latest observation per code. | Pass the question and requested identities; replace latest-only wording; preserve every requested period through final selection. |
| Previous-year CFO discarded | Case 003: FY2025 CFO | The raw extraction contains −6,754.44 INR million for FY2025, but the latest-per-code selection discards it before final output. | Preserve observations by field, company, date, period type and basis. This failure cannot be repaired by wording alone. |

Passing cases cover the standard real report, consolidated versus standalone
distractors, declared-unit conversion and a reported zero. The current correct
definitions and Python unit conversion should be retained.

## Concrete prompt candidate

The [focused extraction candidate](prompts/focused_extraction_candidate.md) asks for
one record per requested identity, reported ROE/ROCE rather than recalculated
ratios, after-tax net CFO, exact source citations, explicit unavailable states,
and preservation of negative signs and zero. It copies raw declared units so
Python converts them exactly once.

**The candidate is drafted; it has not been deployed or benchmarked.** Production
agent behavior and the golden CSV remain unchanged by this review. To test it:

1. Pass the original question and the complete requested identity list to the actor,
   without reference values, expected availability or grades.
2. Extend the raw response schema with per-slot availability, nullable raw values
   and units, and unavailable reasons. Keep inspected pages separate from supporting
   citations. At final emission, use the requested canonical unit even for an
   unavailable fact, as required by the existing fact contract.
3. Preserve each requested identity during selection instead of selecting one
   latest value per metric. Keep current scoring selection separate where needed.
4. Re-run these five cases with unchanged evidence and golden answers. Grade all
   outputs, then repeat the blinded judge review. Record execution errors separately.
5. Check new companies, years, genuinely absent fields, ambiguous ratio units,
   before-tax CFO distractors, zero and negative values. A rise on these development
   cases alone does not establish general performance.

Any gain after steps 1–3 is a **combined prompt, request, schema and selection
experiment**. Do not attribute it to the prompt alone. No improvement percentage
has been measured yet.

## Judge method and limitations

Five actual judge API calls and one separate diagnostic call used the configured
Nebius `moonshotai/Kimi-K3` model, `chat_completions`, reasoning effort `low`, one
attempt per call. The judge uses the same model family as the actor in separate
calls; this is not independent-model or human validation.

Each judge packet contains only the question, requested identities, supplied
evidence and final predictions. Golden numeric answers, expected availability,
prior grades and model identity are withheld. The diagnostic call then receives
the judge results and actual delivered prompts, raw observations, schema and
selection code to identify causes. It is deliberately a separate stage.

The judge uses a task-specific pass/fail/uncertain rubric. The 18/18 agreement is
agreement with the existing rule-and-Codex-source-review labels, **not** agreement
with an independent human panel. Human review of failures and sampled passes is
still needed. This follows the recommendation to use explicit evaluation criteria
and calibrate automated judging against human judgment in the
[OpenAI evaluation guide](https://developers.openai.com/api/docs/guides/evaluation-best-practices).

The judge itself needs review. Its case-002 reasons say `unavailable=true`; the
actual contract is **`available=false, value=null`**. Raw responses are preserved
unaltered. The diagnostic model also marked some ratio wording changes as
prompt-only fixes; the delivered request and latest-only output code show that
end-to-end recovery requires the additional changes above. Its prompt-dilution
claim is a hypothesis, not a measured causal finding.

## Saved evidence and validation

- [All 18 judge reasons and agreement checks](llm_judge_analysis.json)
- [Judge packets, responses and run manifest](../../outputs/evaluations/fundamentals/2026-09-07-llm-judge-01/manifest.json)
- [Raw diagnostic response](../../outputs/evaluations/fundamentals/2026-09-07-judge-diagnosis-01/response.json)
- [Original agent run](../../outputs/evaluations/fundamentals/2026-09-07-all-cases-01/manifest.json)
- [Judge instructions and schemas](../../src/competitive_scoring/evaluation/llm_judge.py)
- [Repeatable judge runner](../../scripts/run_fundamentals_judge.py)

Saved packets, prompts, schemas and responses have content hashes and model
metadata. Seven new offline checks guard answer-key blinding, complete slot
coverage, case/date identity, valid source IDs and consistent verdicts. Together
with existing grader and analysis checks, **47 tests passed**.
