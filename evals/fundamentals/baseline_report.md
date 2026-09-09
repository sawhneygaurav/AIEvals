# First Fundamentals baseline — 7 September 2026

**The current production output does not yet satisfy the seven-field contract.**
The configured model copied four requested concepts correctly, but the final
Fundamentals output exposed none of the seven requested fields. This identifies
specific schema and post-processing gaps before we change the application.

## What was run

- Accepted case: **FUND-PNGJL-FY2026-001**, PNGJL consolidated FY2025–26.
- Model: **moonshotai/Kimi-K3**, using the existing **Nebius Chat Completions** configuration.
- One completed evaluation trial, containing the production dossier request and
  its bounded Fundamentals gap-repair request: **2 provider requests**, about **18.84 seconds**.
- Inputs: five short, verified table excerpts from the saved annual report.
- The production prompt was unchanged. The new seven-field question was not
  inserted into it. This measures the existing application against our desired
  contract, not a newly prompted seven-field extractor.
- No retrieval, PDF parsing, full graph execution or publication occurred.

An earlier attempt failed to connect inside the sandbox. It is saved separately
as `execution_error` and excluded from quality results.

## Expected versus final output

| Field | Golden value | Final output | Diagnosed cause |
|---|---:|---|---|
| ROE | 21% | Missing | Model copied 0.21 as a ratio; the validator requires percent and discards it. |
| ROCE | 30% | Missing | Model copied 0.30 as a ratio; the validator requires percent and discards it. |
| Revenue | ₹10,739.097 crore | Missing | The extraction schema has no revenue code. |
| PAT | ₹409.820 crore | Missing | Correct raw PAT is extracted but not emitted as its own final metric. |
| Shareholders’ equity | ₹1,962.726 crore | Missing | The extraction schema has no shareholders-equity code. |
| Borrowings | ₹1,579.583 crore | Missing | The extraction schema has no total-borrowings code or components. |
| Operating cash flow | −₹716.898 crore | Missing | Correct raw CFO is extracted but not emitted as its own final metric. |

The initial model response contained **six source-supported observations**:
current and prior-year PAT, current and prior-year CFO, and current-year ROE and
ROCE. Codex checked all six against the supplied excerpts. The repair repeated
the two decimal ratios, which were again rejected.

The final agent returned a derived **CFO/PAT of −2.2168x** across two years.
That metric is outside our seven requested fields and cannot substitute for the
individual PAT and cash-flow values. It is retained in the raw result.

## Metrics for this completed trial

| Metric | Result | Meaning |
|---|---:|---|
| Required facts returned correctly | 0 / 7 | The final output contains none of the seven requested metrics. |
| Recall | 0% | All seven required fields are missing. |
| F1 | 0% | Seven false negatives, zero true positives. |
| Precision | Not applicable | No in-scope numeric assertions were returned. |
| Required unit coverage | 0 / 7 | The required monetary and percentage outputs are missing. |
| Unit accuracy on provided fields | Not applicable | No in-scope values were provided. |
| Citation validity / final-output faithfulness | Not applicable | No in-scope final claims exist to review. |
| Case result | **Fail** | The application's current contract needs to be expanded. |

These scores describe final-output coverage on one development case. They do
not mean the model misread every source value. The separate extraction check
shows precisely where correct source facts were lost.

## Additional unit defect found offline

The existing conversion helper converts **10,000 lakh into 1 crore**; the correct
answer is **100 crore**. Its lakh factor is `0.0001`; it must be `0.01`.
An offline probe reproduced the mismatch. Million, crore and billion probes
returned the expected values. No model call was needed for these checks.

## Next implementation steps

1. Extend the extraction contract with revenue, shareholders’ equity and current /
   non-current borrowings, with dates, basis, units and source IDs.
2. Normalize reported decimal ROE/ROCE to percent explicitly; keep the reported
   definition and provenance instead of recomputing ratios from different formulas.
3. Correct lakh-to-crore conversion and expose normalized PAT, CFO, equity, revenue
   and total borrowings as final metrics, without changing investment-score weights.
4. Review the four draft scenarios, then evaluate the changes using the frozen
   cases. Keep this pre-change baseline for comparison.

Production application behavior has not been changed during this evaluation step.
The evaluator and runner are isolated under `competitive_scoring.evaluation` and
`scripts/run_fundamentals_eval.py`.

## Files and validation

- [Five-case review](suite_review.md), [metric definitions](metrics.md).
- [Saved run and fingerprints](../../outputs/evaluations/fundamentals/2026-09-07-baseline-02/metadata.json).
- [Exact model calls](../../outputs/evaluations/fundamentals/2026-09-07-baseline-02/calls.json).
- [Final Fundamentals result](../../outputs/evaluations/fundamentals/2026-09-07-baseline-02/fundamentals.json).
- [Grade](../../outputs/evaluations/fundamentals/2026-09-07-baseline-02/grade.json).
- [Scalar extraction source check](../../outputs/evaluations/fundamentals/2026-09-07-baseline-02/extraction_review.json).
- [Production conversion probe](../../outputs/evaluations/fundamentals/2026-09-07-baseline-02/unit_conversion_probe.json).

**92 focused tests passed**, including 30 evaluator checks and existing agent and
LLM-wrapper checks. Lint passes for the new Python files. Saved prompts were checked
for hidden answer-key fields; none were supplied. Production source fingerprints
still match the versions used in the completed baseline.
