# Review our first real Fundamentals case

**Status: accepted for the first pilot on 7 September 2026.**

The user asked us to keep progressing after reviewing this answer key. See
[suite overview](suite_review.md) for the next four scenarios and baseline status.

Case `FUND-PNGJL-FY2026-001` uses **P N Gadgil Jewellers Limited, consolidated FY2025–26**.
Annual figures cover the year ended 31 March 2026; balance-sheet figures are at
31 March 2026. Management quality will be evaluated separately.

This is scenario 1: all seven requested values are available. The source also
provides useful checks for unit conversion, year selection, and a negative cash-flow sign.

## The question the agent will receive

Using the supplied pages of P N Gadgil Jewellers Limited’s FY2025–26 annual report, provide these seven consolidated figures: ROE, ROCE, revenue from operations, PAT, shareholders’ equity attributable to owners, total borrowings, and net operating cash flow. Use the year ended 31 March 2026 for annual figures and 31 March 2026 for balance sheet figures. For ROE and ROCE, use the reported ratios in consolidated Note 44 and express them as percentages; do not recompute them using another definition. Express all monetary figures in INR crore. Total borrowings means current plus non-current borrowings, including gold loans and excluding separately reported lease liabilities. Operating cash flow means net cash from operating activities after income taxes. Preserve negative signs. Give the source ID and printed page for each figure, and identify any aggregation or unit conversion. If a figure cannot be supported by the supplied evidence, mark it unavailable rather than estimate it.

The agent receives the question and physical PDF pages **92, 93 and 116** of the
saved report. Those pages contain printed pages **180–183 and 228–229**.
It must not receive this review document or the reference JSON.

## Proposed answer key — edit this table first

The ranges below apply to the number only and use the unit in the expected-answer
column. They are **±5% relative**, not ±5 percentage points. Endpoints are inclusive.

| Field | Expected answer | Allowed numerical range | Printed page / PDF page |
|---|---:|---:|---|
| ROE | 21 % | 19.95 to 22.05 | 228 / PDF 116 |
| ROCE | 30 % | 28.5 to 31.5 | 229 / PDF 116 |
| Revenue from operations | 10,739.097 ₹ crore | 10,202.14215 to 11,276.05185 | 181 / PDF 92 |
| PAT | 409.820 ₹ crore | 389.329 to 430.311 | 181 / PDF 92 |
| Shareholders’ equity | 1,962.726 ₹ crore | 1,864.5897 to 2,060.8623 | 180 / PDF 92 |
| Total borrowings | 1,579.583 ₹ crore | 1,500.60385 to 1,658.56215 | 180 / PDF 92 |
| Operating cash flow | -716.898 ₹ crore | -752.7429 to -681.0531 | 182 / PDF 93 |

**Units must match for all seven fields.** The canonical units are `percent` and
`INR_crore`, displayed as `%` and `₹ crore`. A value in ₹ million fails the requested
unit check even when it represents the same amount. Input units may be converted.
The concept, company, date and consolidated basis must also match exactly.
For example, total income must not substitute for revenue from operations merely
because it falls inside the numerical tolerance.

## Definitions behind these answers

- **ROE and ROCE:** consolidated Note 44 reports decimal ratios of **0.21** and
  **0.30**, respectively. Multiplying by 100 gives **21%** and **30%**. We use those
  reported ratios rather than calculate a different version. The report’s overview
  gives **20.9%** and **30.5%** on printed page 21; this first case anchors to Note 44
  because it explicitly identifies the consolidated basis. The overview is outside
  the three supplied PDF pages. Numerical tolerance does not replace evidence support.
- **Revenue:** revenue from operations, not total income.
- **PAT:** the reported profit after tax for the year, not comprehensive income.
- **Shareholders’ equity:** equity attributable to owners at year end. Non-controlling
  interest is nil here, so this equals total equity.
- **Borrowings:** current borrowings **15,692.18** plus non-current borrowings
  **103.65**, both in ₹ million. Their sum is **15,795.83 million**, or
  **₹1,579.583 crore**. This includes gold loans and excludes separately listed leases.
  It is an aggregation of reported lines, not a separately reported total.
- **Operating cash flow:** the published net operating cash outflow after tax,
  **(7,168.98) million**, or **−₹716.898 crore**. Keep the negative sign and use the
  published net line even if summing rounded components produces a small difference.

All monetary source figures are in **₹ million**. Divide by **10** to obtain
**₹ crore**. The JSON answer key preserves the normalized source precision.

## What you should review now

Edit an expected value or definition above if you want something different.
We will reconcile your edits into the reference JSON before freezing this case.
The user accepted this first case for the pilot. New variants remain separately marked as drafts.

The remaining four scenarios and metric definitions are now recorded in the
[suite overview](suite_review.md).

## Saved files and source

- [Agent input and source manifest](pngjl_fy2026_input.json)
- [Hidden answer key and proposed checks](pngjl_fy2026_reference.json)
- [Official annual report](https://cdn.shopify.com/s/files/1/0730/2578/1979/files/P_N_Gadgil_Jewellers_Ltd_AR_25-26-Spread-12mb.pdf?v=1788602897), linked from the
  [company’s investor page](https://www.pngjewellers.com/pages/investors).
- Saved local source: `data/private/evaluation_sources/pngjl_annual_report_2025_26.pdf` (ignored by Git).
- SHA-256: `146a6db89d5a39e890618e6e1dc29943bc1d5a157c330a2bf4feb33e14eacaae`.
- Retrieved: 7 September 2026. Statements signed: 14 May 2026.
  The exact report publication date has not been recorded; the signature date is
  not treated as its publication date.

This is a development case for extraction from frozen evidence, not a retrieval
benchmark. The baseline grades the original production output against all seven
fields and records the schema gaps as missing values. The answer key is unchanged
for the [corrected-agent verification](fix_report.md).
