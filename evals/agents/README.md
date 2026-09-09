# Remaining-agent evaluation

This suite repeats the Fundamentals evaluation workflow with agent-specific
contracts. It adds 44 synthetic development cases for Business, Management,
Valuation, Technicals, Source Research, Fixed Peers, Book scoring, Evidence Audit,
Orchestration and the Final Score Combiner. Fundamentals and Growth retain their
separate existing suite.

Start with [results](results.md), the [expected/predicted CSV](expected_vs_predicted.csv),
and [dataset coverage](dataset_analysis.md). Read the [case review](suite_review.md)
for evidence and answer keys. Codex reviewed these development expectations before
running the actors; human review remains pending. No fixture represents actual
company or market data, including those with simulated primary-source roles.

## What is actually exercised

- Business, Management and Valuation use the **live production shared-dossier
  model call**, then their normal Python output processing. The other dossier
  slices are saved but excluded from that case's grade. Their APIs accept no
  per-case question, so the evaluation question is scope, not a delivered actor
  instruction. The exact delivered prompts are saved.
- Technicals use the real Yahoo adapter with frozen HTTP responses and the real
  indicator/agent code. Monotonic and flat sequences have independently calculated
  expected answers. No current prices are fetched.
- Sources use production collection logic with frozen search/collector adapters.
  This measures ordering, filtering, duplicate handling and error visibility;
  it does not measure internet search recall or source relevance.
- Book cases call the real scoring policy with a synthetic retriever and the
  existing invented demo company. They test coverage, missing evidence and page
  convention handling. **Private-book retrieval relevance is not evaluated.**
- Peer, auditor, combiner and graph cases execute real deterministic code. The
  graph uses the explicit demo data and synthetic book adapter. No report is
  published and no live application refresh is triggered.

## Grading

`inputs.jsonl` contains evidence, caller payloads and task criteria.
`references.jsonl` holds evaluator-only expected answers and deterministic checks.
Neither actor nor judge receives the reference object. Judges see only final
answers, evidence, task criteria and the declared research cutoff. Intermediate
dossier answers cannot earn credit for facts missing from final output.

Rules compare status, citations, dates, units, accounting basis, values and policy
gates. Absence and explicit null are different. Boolean/string/non-finite numeric
values fail numeric comparisons. Valuation values use absolute tolerance 0.0001;
displayed technical values and scoring arithmetic use 0.01. Units and identities
must match exactly. These tolerances concern this suite, not the Fundamentals
suite's separate ±5% policy.

Language cases also get a separate model judge call. Every task criterion receives
pass/fail/uncertain and every final summary, strength, risk and metric receives a
support verdict. Each item is checked for complete coverage and valid source IDs.
An item can contain multiple claims; it is supported only if all its material
factual assertions are supported. Pure process/abstention statements are excluded
from the factual-support denominator. Subjective investment scores are not
treated as factual truth labels; the disclosed Valuation arithmetic is checked
separately. A resolved citation does not by itself establish faithfulness.

The comparison CSV includes `failure_bucket`: **Source prioritization** means
the answer does not clearly prefer the authoritative source; **Missing conflict
disclosure** means the selected value is correct but the disagreement is omitted.
These labels apply to the reviewed failing checks; passing/not-applicable rows
are blank. New unclassified failures and uncertain verdicts are marked separately
for review. Existing grades, reasons and human comments are preserved.

Case success requires all rules, all judge criteria, and factual support to pass.
Uncertainty prevents a pass. Actor and judge execution errors are recorded
separately from incorrect answers. The judge uses the configured actor model in
a separate call, so human calibration and an independent holdout remain needed.
Free-form precision/recall/F1 require an independently annotated atomic-claim
inventory; reporting those scores for this rubric would be misleading.

## Repeat a run

Run from the repository with its virtual environment:

```bash
.venv/bin/python scripts/run_agent_evals.py --mode offline --output-dir outputs/evaluations/agents/my-offline-run
.venv/bin/python scripts/run_agent_evals.py --mode live --output-dir outputs/evaluations/agents/my-live-run
.venv/bin/python scripts/run_agent_evals.py --mode report --source-runs outputs/evaluations/agents/my-offline-run outputs/evaluations/agents/my-live-run --output-dir evals/agents
```

Live mode uses the existing configured provider credentials and incurs actor and
judge API calls. One provider attempt is allowed per logical call. Each run needs
a new directory; prior baselines are preserved. `--case-id BUSINESS-001` can select
a case. A provider failure stops live dispatch rather than repeatedly calling a
failing provider. Report mode makes no API calls, verifies input/reference/output
and judge fingerprints, and rechecks deterministic grades. It refuses duplicate
case results rather than silently choosing a more favorable run. Existing CSV
human comments are preserved by case/check identity.

Per-case artifacts include `input.json`, `reference.json`, `actual.json`,
`metadata.json`, `grade.json` and, for model cases, exact actor and judge
`calls.json` plus the blinded judge packet. They remain under ignored `outputs/`.
These artifacts contain only this suite's synthetic evidence, never the private
book or credentials. Source fingerprints identify the tested production code.

The authoring script `scripts/build_agent_eval_dataset.py` records how the initial
dataset was created. It refuses to replace existing inputs/references; edit and
review those explicitly when versioning a new experiment.
