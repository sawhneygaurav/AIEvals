# Dataset balance

44 synthetic development cases; zero real-company cases, zero holdout cases and zero human-approved cases. One fictional issuer is used for model cases; deterministic graph fixtures reuse the application's invented four-company demo.

| Agent | Cases | Scenarios |
|---|---:|---|
| business | 5 | straightforward, plans_vs_execution, source_conflict, missing_evidence, adversarial |
| management | 5 | inquiry_vs_finding, execution_and_funding, missing_pledge, multiple_dates, missing_evidence |
| valuation | 5 | straightforward, wrong_period, stale_evidence, source_conflict, partial_evidence |
| technicals | 6 | increasing_adjusted, flat, decreasing, fallback_raw, future_tail, insufficient_history |
| peers | 3 | valid_scope, invalid_OTHER_3, invalid_PNGJL_2 |
| sources | 4 | deduplicate, future_result, search_failure, manual_only |
| book | 5 | complete, threshold_80, below_threshold, no_passages, no_company_evidence |
| audit | 4 | clean, dangling_source, low_confidence, provider_warning |
| orchestrator | 2 | complete, failed_book |
| combiner | 5 | complete, unequal_weights, missing_component, missing_growth, manual_review |

Gaps: real saved sources, additional companies, messy/long excerpts, conflicting periods and bases, oscillating RSI series, live retrieval relevance, private-book passage relevance, repeated model runs and unseen holdout examples. Source fixtures test collection logic, not search precision/recall. Book fixtures test policy coverage, not semantic retrieval.
