# Fundamentals pilot: five scenarios

The user authorized running all five cases. Codex checked the four additional answer keys against their supplied evidence and unit conversions without changing any expected values. All cases are development examples; related variants must stay together if a holdout split is added. This review is not independent human labeling.

| Case | Scenario | Correct behavior | Status |
|---|---|---|---|
| FUND-PNGJL-FY2026-001 | all_values_available | Return all seven FY2026 facts. | approved_for_pilot |
| FUND-PNGJL-FY2026-002 | missing_fields | Return four figures; explicitly mark ROE, ROCE and CFO unavailable. | approved_for_pilot |
| FUND-PNGJL-FY2026-003 | two_years | Return 14 facts; never mix FY2025 and FY2026. | approved_for_pilot |
| FUND-SYN-004 | accounting_basis | Use consolidated synthetic figures, ignoring standalone distractors. | approved_for_pilot |
| FUND-SYN-005 | input_units | Convert lakh ÷100 and million ÷10; preserve exact zero CFO. | approved_for_pilot |

Cases 1–3 use verified excerpts from the saved PNGJL annual report. Cases 4–5 are clearly labelled synthetic data for a fictional company, making differences easy to spot. The first case’s numerical answer key is unchanged; the runner supplies transcribed table excerpts rather than PDF images. This measures the agent’s use of supplied text, not PDF parsing or retrieval.

## Values to edit

### FUND-PNGJL-FY2026-002

| Field | Period end | Expected value | Unit |
|---|---|---:|---|
| roe | 2026-03-31 | Unavailable | percent |
| roce | 2026-03-31 | Unavailable | percent |
| revenue | 2026-03-31 | 10739.097 | INR_crore |
| pat | 2026-03-31 | 409.82 | INR_crore |
| shareholders_equity | 2026-03-31 | 1962.726 | INR_crore |
| borrowings | 2026-03-31 | 1579.583 | INR_crore |
| operating_cash_flow | 2026-03-31 | Unavailable | INR_crore |

### FUND-PNGJL-FY2026-003

| Field | Period end | Expected value | Unit |
|---|---|---:|---|
| roe | 2026-03-31 | 21.0 | percent |
| roce | 2026-03-31 | 30.0 | percent |
| revenue | 2026-03-31 | 10739.097 | INR_crore |
| pat | 2026-03-31 | 409.82 | INR_crore |
| shareholders_equity | 2026-03-31 | 1962.726 | INR_crore |
| borrowings | 2026-03-31 | 1579.583 | INR_crore |
| operating_cash_flow | 2026-03-31 | -716.898 | INR_crore |
| roe | 2025-03-31 | 14 | percent |
| roce | 2025-03-31 | 20 | percent |
| revenue | 2025-03-31 | 7693.468 | INR_crore |
| pat | 2025-03-31 | 218.268 | INR_crore |
| shareholders_equity | 2025-03-31 | 1553.938 | INR_crore |
| borrowings | 2025-03-31 | 823.087 | INR_crore |
| operating_cash_flow | 2025-03-31 | -675.444 | INR_crore |

### FUND-SYN-004

| Field | Period end | Expected value | Unit |
|---|---|---:|---|
| roe | 2026-03-31 | 20 | percent |
| roce | 2026-03-31 | 25 | percent |
| revenue | 2026-03-31 | 1000 | INR_crore |
| pat | 2026-03-31 | 100 | INR_crore |
| shareholders_equity | 2026-03-31 | 500 | INR_crore |
| borrowings | 2026-03-31 | 200 | INR_crore |
| operating_cash_flow | 2026-03-31 | 80 | INR_crore |

### FUND-SYN-005

| Field | Period end | Expected value | Unit |
|---|---|---:|---|
| roe | 2026-03-31 | 25 | percent |
| roce | 2026-03-31 | 30 | percent |
| revenue | 2026-03-31 | 10000 | INR_crore |
| pat | 2026-03-31 | 500 | INR_crore |
| shareholders_equity | 2026-03-31 | 2000 | INR_crore |
| borrowings | 2026-03-31 | 150 | INR_crore |
| operating_cash_flow | 2026-03-31 | 0 | INR_crore |

## Scoring and execution

See [metrics](metrics.md), [agent inputs](suite_inputs.jsonl), and [hidden references](suite_references.jsonl). Inputs and references are deliberately separate. The [original baseline](baseline_report.md) and [first corrected-agent verification](fix_report.md) covered case 1. The [latest run](all_cases_report.md) covers all five cases through the same production dossier and Fundamentals post-processing.
