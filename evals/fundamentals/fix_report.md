# Fundamentals output fix — 7 September 2026

The corrected production agent returns all seven requested values for the accepted
PNGJL consolidated FY2025–26 case. All seven pass the agreed ±5% relative tolerance,
exact unit and identity checks, and Codex's source check. The answer key and supplied
evidence are unchanged from the original baseline.

| Field | Expected | Predicted | Unit | Result |
|---|---:|---:|---|---|
| ROE | 21 | 21 | percent | PASS |
| ROCE | 30 | 30 | percent | PASS |
| Revenue from operations | 10739.097 | 10739.097 | INR crore | PASS |
| PAT | 409.820 | 409.820 | INR crore | PASS |
| Equity attributable to owners | 1962.726 | 1962.726 | INR crore | PASS |
| Borrowings | 1579.583 | 1579.583 | INR crore | PASS |
| Net operating cash flow | -716.898 | -716.898 | INR crore | PASS |

Annual amounts and ratios refer to the year ended 31 March 2026. Equity and
borrowings are point-in-time values at that date. All values are consolidated.

## What changed

- The extraction schema and shared production instructions include revenue,
  equity, borrowings and its current/non-current components. PAT and operating
  cash flow remain visible in the final metrics, even when CFO/PAT is available.
- Python converts reported ROE/ROCE decimal ratios to percent and INR amounts
  to crore. The lakh-to-crore factor is corrected to 0.01. Decimal arithmetic
  avoids binary floating-point noise during scale conversion. Source amounts
  remain recorded in full in the audit notes.
- Borrowings are summed only when both explicit components share the same
  source, date, basis and point-in-time period. A missing component is not zero.
  The prompt distinguishes borrowings from leases and net debt, revenue from
  total income, PAT from comprehensive income, and net operating cash flow
  from the before-tax subtotal.
- Balance-sheet keywords retain relevant evidence in compact prompts. The one
  repair pass can request omitted statement amounts even if the investment
  score already has its required ratios.
- Statement amounts do not increase the investment score's coverage or
  confidence. Scoring weights and gates remain the same.

## Verification and limits

The final live run used Nebius `moonshotai/Kimi-K3`, chat completions, low reasoning,
the same frozen input/reference fingerprints as the baseline, and one attempt per
logical call. It made two calls in 9.34 seconds: shared extraction and the existing
gap repair, which returned no additional observations. No provider diagnostics occurred.

The final run has 7 true positives, 0 false positives and 0 false negatives.
Precision, recall, F1, numeric accuracy and unit coverage are 1.0 for the seven
requested structured fields. Citation validity and source-checked faithfulness
are 1.0. The source review was performed by Codex, not a human or an independent
blind reviewer. Qualitative text and the additional CFO/PAT scoring metric are
outside this pilot's faithfulness denominator.

The original baseline returned 0 of the 7 fields and remains preserved. An initial
sandbox connection failure is excluded from quality results. An intermediate fixed
run also matched all seven values; the final run follows a refinement to decimal
precision and full source-number audit notes. This is still one development case,
not a holdout result or a measurement of repeatability.

All 265 automated tests passed, including 27 new regression cases for normalized
output, mixed scales, invalid observations, negative and zero values, borrowing
compatibility, repair behavior, freshness and unchanged investment scoring.

The production block still has `status=insufficient_evidence` and `score=null`:
the supplied pages do not provide the growth and protection metrics required
for an investment quality score. This does not suppress the seven supported
financial facts. Four draft evaluation scenarios (35 field slots) remain unrun.
No retrieval, PDF parsing, full graph refresh or additional company was tested.

## Saved evidence

- [Final model output](../../outputs/evaluations/fundamentals/2026-09-07-fixed-03/fundamentals.json)
- [Grade](../../outputs/evaluations/fundamentals/2026-09-07-fixed-03/grade.json)
- [Exact calls](../../outputs/evaluations/fundamentals/2026-09-07-fixed-03/calls.json)
- [Run settings and fingerprints](../../outputs/evaluations/fundamentals/2026-09-07-fixed-03/metadata.json)
- [Seven source checks](../../outputs/evaluations/fundamentals/2026-09-07-fixed-03/evidence_review.json)
- [Original baseline](baseline_report.md)
- [Current comparison and editable human comments](golden_dataset_with_predictions.csv)

The comparison refresh changes only predictions, predicted units, results and
reasons for the accepted case. Expected values, human comments and all draft rows
are preserved. Dataset distribution counts are unchanged.
