# Remaining-agent baseline

All 44 cases use fictional development fixtures. Language agents use the configured live model; technical, source, book and graph inputs use frozen adapters. No live search or private-book semantic retrieval is measured.

| Agent | Cases pass / completed | Rule checks pass / checked | Judge criteria pass / checked | Supported / factual claim spans | Errors | Unrun |
|---|---:|---:|---:|---:|---:|---:|
| business | 4/5 | 12/12 | 11/12 | 14/14 | 0 | 0 |
| management | 5/5 | 12/12 | 14/14 | 11/11 | 0 | 0 |
| valuation | 4/5 | 52/52 | 9/10 | 14/14 | 0 | 0 |
| technicals | 6/6 | 66/66 | 0/0 | 0/0 | 0 | 0 |
| peers | 3/3 | 6/6 | 0/0 | 0/0 | 0 | 0 |
| sources | 4/4 | 11/11 | 0/0 | 0/0 | 0 | 0 |
| book | 5/5 | 18/18 | 0/0 | 0/0 | 0 | 0 |
| audit | 4/4 | 7/7 | 0/0 | 0/0 | 0 | 0 |
| orchestrator | 2/2 | 12/12 | 0/0 | 0/0 | 0 | 0 |
| combiner | 5/5 | 10/10 | 0/0 | 0/0 | 0 | 0 |

A case passes only when every deterministic check and judge criterion passes, and all judged factual spans are supported. Judge uncertainty prevents a pass. Citation resolution is a structural check, not proof of faithfulness. Pure abstentions/process statements are excluded from factual support; omissions cannot earn faithfulness credit.

Numbers in the judge columns are same-model assessments, not independent human validation. Human calibration is pending. Free-form precision, recall and F1 are not reported: a complete independently annotated atomic-claim inventory does not yet exist. Rule checks include identities, units and gates; they are not interchangeable with fact counts.

Business, Management and Valuation receive their existing production instructions and evidence. Their interfaces have no per-case question argument. Any missing requested content must be interpreted with that constraint. Actor and judge errors remain separate from quality failures.

## Non-passing cases

- BUSINESS-003: fail
- VALUATION-004: fail

## Failure buckets

| Bucket | Checks |
|---|---:|
| Missing conflict disclosure | 1 |
| Source prioritization | 1 |

See [all expectations and predictions](expected_vs_predicted.csv), [reviewable cases](suite_review.md), and [structured results](results.json).
