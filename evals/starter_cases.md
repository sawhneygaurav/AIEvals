# Sample evaluation dataset — edit together

**Status: six synthetic drafts awaiting your review.** All company figures and source excerpts are fictional. The scoring weights match the existing project. These are not investment research or real book quotations.

These are earlier synthetic teaching examples. Our current review is the [real PNGJL FY2025–26 Fundamentals case](fundamentals/pngjl_fy2026_review.md), which uses the agreed ±5% relative tolerance and exact output units. The thresholds below belong only to these older examples.

| Case | Agent/stage | What it tests |
|---|---|---|
| FUND-001 | Fundamentals | Correct growth numbers, periods, and citations |
| FUND-002 | Fundamentals | Saying unavailable when evidence is missing |
| BUS-001 | Business | Distinguishing completed openings from plans |
| MGT-001 | Management | Describing an inquiry without inventing a finding |
| BOOK-001 | Book RAG | Separating a teaching principle from company facts |
| SCORE-001 | Final scoring | Applying the existing 80/20 formula |

## Our first exam: FUND-001

Imagine giving the Fundamentals Agent this envelope:

| Fictional consolidated results (INR crore) | FY2025 | FY2026 |
|---|---:|---:|
| Revenue | 1,000 | 1,200 |
| Net profit (PAT) | 80 | 100 |

The supplied report also explicitly states **revenue growth 20%** and **PAT growth 25%**. This matches the live extraction agent's job: copy supported reported facts. Our independent arithmetic check confirms those rates from the table.

**Your first decision:** Is the proposed answer below complete enough? For example, should missing the financial years or the citation fail this case?

## FUND-001 — Fundamentals Agent

**Review status:** Draft | **Type:** straightforward | **Cutoff:** 2026-09-01

**Question for the agent**

What reported revenue and net-profit growth should the agent extract for FY2026 versus FY2025?

**Evidence supplied to the agent**

**SYN-FIN-001 — Fictional Aster Jewellers: annual results · page 4**

> All figures are fictional. Consolidated results; amounts in INR crore. FY2025 ended 31 March 2025: revenue 1,000; net profit (PAT) 80. FY2026 ended 31 March 2026: revenue 1,200; net profit (PAT) 100. Reported FY2026 year-on-year revenue growth: 20%. Reported FY2026 year-on-year PAT growth: 25%.

**Suggested correct answer — edit this**

For fictional Aster Jewellers, consolidated revenue grew 20% and net profit grew 25% in FY2026 compared with FY2025. These are one-year growth rates. [SYN-FIN-001, p. 4]

**Must-have facts — edit these**

```json
{
  "revenue_growth_pct": 20,
  "profit_growth_pct": 25,
  "current_period": "FY2026",
  "comparison_period": "FY2025",
  "accounting_basis": "consolidated",
  "source_ids": [
    "SYN-FIN-001"
  ]
}
```

**Pass only when — edit these rules**

- Report revenue growth as 20% and net-profit growth as 25%; numerical tolerance is 0.01 percentage points.
- Identify FY2026 versus FY2025 and consolidated accounting basis.
- Cite SYN-FIN-001; equivalent wording is allowed.

**Fail when — edit these rules**

- Swap revenue growth and profit growth, or report a decimal ratio as a percentage without conversion.
- Describe these one-year rates as three-year CAGR or quarterly growth.
- Give a fabricated or unrelated source.

**How we would grade it**

Deterministic checks for values, periods, basis and source ID; human review for faithful wording.

**Your edits / questions:**

[Write here, or send your changes in chat.]

**Approval:** Draft — awaiting your review.

## FUND-002 — Fundamentals Agent

**Review status:** Draft | **Type:** tricky | **Cutoff:** 2026-09-01

**Question for the agent**

What is FY2026 year-on-year net-profit growth? Use only the supplied evidence; no web search is available.

**Evidence supplied to the agent**

**SYN-FIN-002 — Fictional Aster Jewellers: incomplete profit excerpt · page 5**

> All figures are fictional. Consolidated FY2026 net profit (PAT): INR 100 crore. FY2025 net profit is not supplied. No profit-growth rate is supplied.

**Suggested correct answer — edit this**

FY2026 net profit is INR 100 crore, but year-on-year profit growth is unavailable because neither FY2025 profit nor a reported growth rate is provided. [SYN-FIN-002, p. 5]

**Must-have facts — edit these**

```json
{
  "current_pat_crore": 100,
  "profit_growth_pct": null,
  "missing_fields": [
    "FY2025 net profit or a reported comparable profit-growth rate"
  ],
  "source_ids": [
    "SYN-FIN-002"
  ]
}
```

**Pass only when — edit these rules**

- Return unavailable/null for growth and explain the missing comparison evidence.
- Retain the verified INR 100 crore FY2026 profit with its citation.

**Fail when — edit these rules**

- Return 0%, 20%, 25%, or another invented growth number.
- Treat a missing previous-year value as zero.
- Claim to have searched or verified a source outside the supplied evidence.

**How we would grade it**

Deterministic null/value/citation checks plus human review of the explanation.

**Your edits / questions:**

[Write here, or send your changes in chat.]

**Approval:** Draft — awaiting your review.

## BUS-001 — Business Agent

**Review status:** Draft | **Type:** straightforward | **Cutoff:** 2026-09-01

**Question for the agent**

Summarize the company's completed store expansion and its future plan as of 1 September 2026.

**Evidence supplied to the agent**

**SYN-BUS-001 — Fictional Aster Jewellers: expansion announcement · page 2**

> All figures are fictional. Three stores opened during the quarter ended 30 June 2026. Management plans to open 20 additional stores during FY2027. The 20-store plan is a forward-looking target, not a completed opening count.

**Suggested correct answer — edit this**

Three stores opened in the quarter ended 30 June 2026. Management plans 20 additional openings in FY2027; that target has not been confirmed as completed. [SYN-BUS-001, p. 2]

**Must-have facts — edit these**

```json
{
  "completed_openings": 3,
  "completed_period_end": "2026-06-30",
  "planned_openings": 20,
  "plan_period": "FY2027",
  "source_ids": [
    "SYN-BUS-001"
  ]
}
```

**Pass only when — edit these rules**

- Preserve both counts and their distinct time periods.
- Clearly distinguish completed openings from management's future target.
- Support both statements with SYN-BUS-001.

**Fail when — edit these rules**

- Say that 20 or 23 stores have already opened.
- Present the FY2027 plan as guaranteed growth or completed execution.

**How we would grade it**

Deterministic count/period checks plus human review for plan-versus-fact accuracy.

**Your edits / questions:**

[Write here, or send your changes in chat.]

**Approval:** Draft — awaiting your review.

## MGT-001 — Management Agent

**Review status:** Draft | **Type:** tricky | **Cutoff:** 2026-09-01

**Question for the agent**

Describe the governance issue and what it does and does not establish.

**Evidence supplied to the agent**

**SYN-MGT-001 — Fictional Aster Jewellers: regulatory clarification notice · page 1**

> Fictional notice. The regulator has requested clarification about a related-party transaction. The inquiry is ongoing as of 31 August 2026. The notice contains no finding of fraud, no conviction and no final penalty.

**Suggested correct answer — edit this**

An ongoing regulatory inquiry into a related-party transaction warrants monitoring. The supplied notice establishes an inquiry, not a finding of fraud or a final penalty. [SYN-MGT-001, p. 1]

**Must-have facts — edit these**

```json
{
  "inquiry_status": "ongoing",
  "fraud_finding_in_supplied_evidence": false,
  "final_penalty_in_supplied_evidence": false,
  "source_ids": [
    "SYN-MGT-001"
  ]
}
```

**Pass only when — edit these rules**

- Describe the inquiry as ongoing and cite the notice.
- Explain the governance uncertainty without asserting either guilt or exoneration.

**Fail when — edit these rules**

- Say management committed fraud or was convicted.
- Say the company was cleared or that the inquiry poses no risk.
- Invent a penalty amount or an external update.

**How we would grade it**

Human evidence-based review; source-ID checks alone cannot judge the claim.

**Your edits / questions:**

[Write here, or send your changes in chat.]

**Approval:** Draft — awaiting your review.

## BOOK-001 — Book RAG Scorer

**Review status:** Draft | **Type:** tricky | **Cutoff:** 2026-09-01

**Question for the agent**

Choose the relevant teaching principle and explain cash conversion using the company evidence. These training notes are synthetic and are not excerpts from Peaceful Investing.

**Evidence supplied to the agent**

**SYN-POLICY-CASH — Synthetic training note: cash conversion · page 7**

> Teaching rule written for this dataset: compare operating cash flow with reported profit. Persistently weak conversion deserves investigation. A single weak year does not by itself establish fraud.

**SYN-POLICY-PRICE — Synthetic training note: valuation (distractor) · page 9**

> Teaching rule written for this dataset: compare the price paid with business quality and documented valuation evidence.

**SYN-FIN-003 — Fictional Aster Jewellers: cash flow and profit · page 6**

> All figures are fictional. Consolidated FY2026: operating cash flow INR 10 crore; net profit (PAT) INR 100 crore. Reported operating-cash-flow/PAT ratio: 0.10x. No prior-year cash-flow figures or explanation for the gap are supplied.

**Suggested correct answer — edit this**

FY2026 operating cash flow was INR 10 crore against PAT of INR 100 crore, a ratio of 0.10x. [SYN-FIN-003, p. 6] The relevant cash-conversion teaching note says weak conversion deserves investigation; one year does not establish persistent weakness or fraud. [SYN-POLICY-CASH, p. 7] The cause is not established by the supplied evidence.

**Must-have facts — edit these**

```json
{
  "cfo_pat_ratio": 0.1,
  "relevant_principle_source_id": "SYN-POLICY-CASH",
  "company_fact_source_id": "SYN-FIN-003",
  "persistent_weakness_established": false,
  "fraud_established": false
}
```

**Pass only when — edit these rules**

- Select the cash-conversion note and cite it separately from the company financial evidence.
- Preserve the 0.10x ratio and FY2026 consolidated period/basis.
- Explain why the gap deserves investigation without inventing its cause.

**Fail when — edit these rules**

- Use the valuation distractor as the sole principle supporting the cash-flow assessment.
- Treat the teaching note as evidence of the company's financial numbers.
- Invent a quotation or page from the real private book.
- Claim fraud or a multi-year pattern from a single year of data.

**How we would grade it**

Deterministic ratio and source checks plus human review of retrieval relevance and application. A later integration will test the actual retriever against private, reviewed passages.

**Your edits / questions:**

[Write here, or send your changes in chat.]

**Approval:** Draft — awaiting your review.

## SCORE-001 — Final Score Combiner

**Review status:** Draft | **Type:** straightforward | **Cutoff:** 2026-09-01

**Question for the agent**

Calculate the final score using the approved core/book weighting.

**Evidence supplied to the agent**

**SYN-SCORE-001 — Synthetic scoring inputs**

> Fictional complete research result: core score 80/100; book-alignment score 60/100. All core components are supported. Book evidence coverage is 100%; book decision status is complete. Evidence audit passed.

**PROJECT-SCORE-POLICY — Project scoring policy**

> The project formula is Final = 0.80 times Core + 0.20 times Book. Missing required evidence or an incomplete book decision prevents a final score.

**Suggested correct answer — edit this**

Final score = (80 x 0.80) + (60 x 0.20) = 76.00 out of 100. This is the project's weighted score, not a prediction of investment returns.

**Must-have facts — edit these**

```json
{
  "final_score": 76.0,
  "core_weight": 0.8,
  "book_weight": 0.2
}
```

**Pass only when — edit these rules**

- Return a final score of 76.00/100, within 0.01 points.
- Use the supplied 80% core and 20% book weights; accept an equivalent calculation.

**Fail when — edit these rules**

- Return 70 from an unweighted average, or change the weights.
- Treat the score as a guaranteed future investment return.

**How we would grade it**

Deterministic arithmetic check; explanatory prose, if present, receives human review.

**Your edits / questions:**

[Write here, or send your changes in chat.]

**Approval:** Draft — awaiting your review.

## What happens after your edits?

We update the answer key and JSONL together, record your approval per case, and then build the runner against the agreed rules. Future cases will cover peer selection, source quality, valuation, technical indicators, orchestration, audit failures, and report publication.

A case with correct numbers but an unsupported explanation should not silently pass. The framework will keep automatic checks and any pending human review visible.
